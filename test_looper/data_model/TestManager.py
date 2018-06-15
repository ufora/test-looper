import collections
import logging
import random
import time
import traceback
import simplejson
import threading
import fnmatch
import textwrap
import re
import os
from test_looper.core.hash import sha_hash
import test_looper.data_model.SingleTestRunResult as SingleTestRunResult
import test_looper.core.Bitstring as Bitstring
import test_looper.core.object_database as object_database
import test_looper.core.algebraic as algebraic
import test_looper.core.machine_management.MachineManagement as MachineManagement
import test_looper.data_model.Types as Types
import test_looper.data_model.BranchPinning as BranchPinning
import test_looper.data_model.TestDefinitionResolver as TestDefinitionResolver

pendingVeryHigh = Types.BackgroundTaskStatus.PendingVeryHigh()
pendingHigh = Types.BackgroundTaskStatus.PendingHigh()
pendingMedium = Types.BackgroundTaskStatus.PendingMedium()
pendingLow = Types.BackgroundTaskStatus.PendingLow()
pendingVeryLow = Types.BackgroundTaskStatus.PendingVeryLow()
running = Types.BackgroundTaskStatus.Running()

MAX_TEST_PRIORITY = 2
TEST_TIMEOUT_SECONDS = 60
IDLE_TIME_BEFORE_SHUTDOWN = 180
MAX_LOG_MESSAGES_PER_TEST = 100000
MACHINE_TIMEOUT_SECONDS = 600
MACHINE_TIMEOUT_SECONDS_FIRST_HEARTBEAT = 1200
DISABLE_MACHINE_TERMINATION = False
DEAD_WORKER_PRUNE_INTERVAL = 600
AMI_CHECK_INTERVAL = 30

OLDEST_TIMESTAMP_WITH_TESTS = 1500000000
MAX_GIT_CONNECTIONS = 4
class MessageBuffer:
    def __init__(self, name):
        self.name = name
        self._lock = threading.RLock()
        self._subscribers = set()
        self._messages = []
    
    def setTotalMessages(self, all_data):
        with self._lock:
            bytes_so_far = sum([len(m) for m in self._messages])
            self.addMessage(all_data[bytes_so_far:])

    def addMessage(self, msg):
        with self._lock:
            toRemove = []
            for l in self._subscribers:
                try:
                    l(msg)
                except:
                    logging.error("Failed to write message %s: %s", self.name, traceback.format_exc())
                    toRemove.append(l)
            for l in toRemove:
                self._subscribers.discard(l)

            self._messages.append(msg)

    def subscribe(self, onMessage):
        try:
            with self._lock:
                toSend = list(self._messages)

            #send all the existing messages outside of the lock since this could take a while
            for m in toSend:
                onMessage(m)

            with self._lock:
                #catch up on any new messages
                for m in self._messages[len(toSend):]:
                    onMessage(m)

                self._subscribers.add(onMessage)
        except:
            logging.error("Failed in subscribe: \n%s", traceback.format_exc())

    def unsubscribe(self, onMessage):
        with self._lock:
            self._subscribers.discard(onMessage)


class DeploymentStream:
    def __init__(self, deploymentId):
        self.deploymentId = deploymentId
        self._messagesFromClient = MessageBuffer("Client messages for %s" % self.deploymentId)
        self._messagesFromDeployment = MessageBuffer("Deployment messages for %s" % self.deploymentId)

    def clientCount(self):
        return len(self._messagesFromDeployment._subscribers)

    def addMessageFromClient(self, msg):
        self._messagesFromClient.addMessage(msg)

    def allMessagesFromDeploymentFromStart(self, all_data):
        self._messagesFromDeployment.setTotalMessages(all_data)

    def addMessageFromDeployment(self, msg):
        self._messagesFromDeployment.addMessage(msg)

    def subscribeToDeploymentMessages(self, onMessage):
        self._messagesFromDeployment.subscribe(onMessage)

    def subscribeToClientMessages(self, onMessage):
        self._messagesFromClient.subscribe(onMessage)

    def unsubscribeFromDeploymentMessages(self, onMessage):
        self._messagesFromDeployment.unsubscribe(onMessage)

    def unsubscribeFromClientMessages(self, onMessage):
        self._messagesFromClient.unsubscribe(onMessage)


class HeartbeatHandler(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.logs = {}
        self.timestamps = {}
        self.listeners = {}
        
    def getMostRecentTestHeartbeats(self, testId):
        with self.lock:
            if testId in self.logs:
                res = "".join(self.logs[testId])
                if len(res) < 10 * 1024:
                    return res
                return res[-10*1024:]
            return ""        
        

    def getAllLogsFor(self, testId):
        with self.lock:
            if testId in self.logs:
                return "".join(self.logs[testId])
            return ""        
        
    def addListener(self, testId, listener):
        with self.lock:
            if testId not in self.listeners:
                self.listeners[testId] = []

            if testId in self.logs:
                try:
                    listener("".join(self.logs[testId]))
                except:
                    logging.error("Failed to write log message for testId %s to listener %s:\n%s", testId, listener, traceback.format_exc())
                    return
                
                self.listeners[testId].append(listener)

    def testFinished(self, testId):
        #reset the logs since they're not live anymore.
        #self.logs[testId] = []
        pass
        
    def testHeartbeatReinitialized(self, testId, timestamp, logMessagesFromStart):
        with self.lock:
            message_len = sum([len(x) for x in self.logs.get(testId, [])])
            self.testHeartbeat(testId, timestamp, logMessagesFromStart[message_len:])


    def testHeartbeat(self, testId, timestamp, logMessage=None):
        """Record the log message, fire off socket connections, and return whether to log the heartbeat in the database."""
        with self.lock:
            if testId not in self.timestamps:
                self.timestamps[testId] = timestamp
                self.logs[testId] = []

            if logMessage is not None:
                self.logs[testId].append(logMessage)
                if len(self.logs[testId]) > MAX_LOG_MESSAGES_PER_TEST:
                    self.logs[testId] = self.logs[testId][100:]

                new_listeners = []
                for l in self.listeners.get(testId, []):
                    try:
                        l(logMessage)
                        new_listeners.append(l)
                    except:
                        logging.error("Failed to write log message for testId %s to listener %s:\n%s", testId, l, traceback.format_exc())
                self.listeners[testId] = new_listeners


            if timestamp - self.timestamps[testId] > 5.0:
                self.timestamps[testId] = timestamp
                return True
            else:
                return False


version_pattern = re.compile(".*([0-9-._]+).*")

class TestManager(object):
    def __init__(self, server_port_config, source_control, machine_management, kv_store, initialTimestamp=None):
        self._repoCommitCalcCache = {}

        self.initialTimestamp = initialTimestamp or time.time()
        self.lastWorkerPruneOperation = self.initialTimestamp
        self.lastAmiCheckTimestamp = 0

        self.server_port_config = server_port_config
        self.source_control = source_control
        self.machine_management = machine_management

        self.database = object_database.Database(kv_store)
        Types.setup_types(self.database)

        self.writelock = threading.RLock()

        self.heartbeatHandler = HeartbeatHandler()

        self.deploymentStreams = {}

        self.commitTestCache_ = {}

    def allTestsForCommit(self, commit):
        if not commit.data:
            return []

        return commit.data.tests.values()

    def allTestsDependedOnByTest(self, test):
        res = []

        for dep in self.database.TestDependency.lookupAll(test=test):
            res.append(dep.dependsOn)

        return res

    def bestCommitName(self, commit):
        branch, name = self.bestCommitBranchAndName(commit)
        if not branch:
            return name
        return branch.branchname + name

    def bestCommitBranchAndName(self, commit):
        if commit.repo in self._repoCommitCalcCache and \
                commit in self._repoCommitCalcCache[commit.repo]:
            return self._repoCommitCalcCache[commit.repo][commit]


        branches = self.commitFindAllBranches(commit)

        branches_by_name = {b.branchname: v for b,v in branches.items()}

        if not branches:
            return None, str(commit.hash)[:10]

        def masteryness(branchname):
            fields = []

            if branchname == "master":
                fields.append(0)
            elif branchname == "svn-master":
                fields.append(1)
            elif 'master' in branchname:
                fields.append(2)
            elif 'trunk' in branchname:
                fields.append(3)

            #look for a version number
            match = version_pattern.match(branchname)
            if not match:
                #sort last
                fields.append("XXX")
            else:
                version = match.groups()[0]
                for char in ".-_":
                    version = version.replace(char," ")
                fields.append(version.split(" "))

            #shortest
            fields.append(len(branchname + branches_by_name[branchname]))

            return tuple(fields)
            
        #pick the least feature-branchy name we can
        best_branch = sorted(branches, key=lambda b: masteryness(b.branchname))[0]

        if commit.repo not in self._repoCommitCalcCache:
            self._repoCommitCalcCache[commit.repo] = {}
        self._repoCommitCalcCache[commit.repo][commit] = (best_branch, branches[best_branch])

        return best_branch, branches[best_branch]

    def commitFindAllBranches(self, commit):
        childCommits = {}

        def children(c):
            toCheck = {}
            for r in self.database.CommitRelationship.lookupAll(parent=c):
                child = r.child

                ix = child.data.parents.index(c)

                toCheck[child] = "^" + str(ix+1) if ix > 0 else "~"

            return toCheck

        def compress_pathback(path):
            start = None
            i = 0
            while i <= len(path):
                if i < len(path) and path[i] == "~":
                    if start is None:
                        start = i
                    i += 1
                else:
                    if start is not None and i-start > 1:
                        path = path[:start] + "~" + str(i - start) + path[i:]
                        i = start+1
                        start = None
                    else:
                        i += 1

            return path

        branches = {}

        def check(c, path_back):
            if c not in childCommits or len(path_back) < len(childCommits[c]):
                childCommits[c] = path_back

                for branch in self.database.Branch.lookupAll(head=c):
                    branches[branch] = compress_pathback(path_back)

                for child, to_add in children(c).items():
                    check(child, to_add + path_back)

        check(commit, "")

        return branches

    def streamForDeployment(self, deploymentId):
        with self.writelock:
            if deploymentId not in self.deploymentStreams:
                self.deploymentStreams[deploymentId] = DeploymentStream(deploymentId)
            return self.deploymentStreams[deploymentId]

    def transaction_and_lock(self):
        t = [None]
        
        class Scope:
            def __enter__(scope):
                self.writelock.__enter__()
                t[0] = self.database.transaction()
                t[0].__enter__()

            def __exit__(scope, *args):
                try:
                    t[0].__exit__(*args)
                finally:
                    self.writelock.__exit__(*args)

        return Scope()

    def getNCommits(self, commit, N, direction="below", restrictTo=None):
        """Do a breadth-first search around 'commit'"""

        assert direction in ("above", "below")

        commits = []
        seen = set()
        frontier = [commit]

        while frontier and len(commits) < N:
            c = frontier.pop(0)
            if c not in seen and (not restrictTo or c in restrictTo):
                seen.add(c)
                commits.append(c)
                if direction == "below":
                    if c.data:
                        frontier.extend(c.data.parents)
                else:
                    frontier.extend([
                        r.child for r in self.database.CommitRelationship.lookupAll(parent=c)
                        ])

        return commits[1:]

    def topNPrioritizedCommitsForBranch(self, branch, commitCount, maxLookback=500):
        res = []
        head = branch.head

        while head and len(res) < commitCount and maxLookback > 0:
            maxLookback -= 1

            if head.userEnabledTestSets:
                res.append(head)
            if head.data and head.data.parents:
                head = head.data.parents[0]
            else:
                head = None

        return res

    def commitsToDisplayForBranch(self, branch, max_commits):
        commits = set()
        ordered = []
        new = [branch.head]
        while new:
            n = new.pop(0)

            if n and n not in commits:
                commits.add(n)
                ordered.append(n)

                if max_commits is not None and len(ordered) >= max_commits:
                    return ordered

                if n.data:
                    for child in n.data.parents:
                        new.append(child)

        return ordered

    def shutdownDeployment(self, deploymentId, timestamp):
        with self.transaction_and_lock():
            d = self.database.Deployment(deploymentId)
            if not d.exists():
                raise Exception("Deployment %s doesn't exist" % deploymentId)

            self._cancelDeployment(d, timestamp)

    def _cancelDeployment(self, deployment, timestamp):
        if not deployment.isAlive:
            logging.warn("Tried to cancel a deployment that's already shut down.")
            return

        deploymentId = deployment._identity
        cat = self._machineCategoryForTest(deployment.test)
        cat.desired = cat.desired - 1

        self.streamForDeployment(deploymentId).addMessageFromDeployment(
            "\r\n\r\n" + 
            time.asctime(time.gmtime(timestamp)) + " TestLooper> Session Terminated\r\n\r\n"
            )

        deployment.isAlive = False

        logging.info("Canceling deployment %s. Desired count for category %s/%s/%s is now %s vs booted %s", 
                deploymentId, cat._identity[:6], cat.hardware, cat.os, cat.desired, cat.booted)

        if deployment.machine:
            deployment.machine.lastTestCompleted = timestamp

            logging.info("Setting last test completed on %s ", deployment.machine.machineId, timestamp)

            os = deployment.machine.os
        
            if (os.matches.WindowsVM or os.matches.LinuxVM):
                #we need to shut down this machine since it has a setup script
                if not DISABLE_MACHINE_TERMINATION:
                    self._terminateMachine(deployment.machine, timestamp)

        self._scheduleBootCheck()
        self._shutdownMachinesIfNecessary(timestamp)

    def createDeployment(self, hash, timestamp):
        with self.transaction_and_lock():
            test = self.database.Test.lookupAny(hash=hash)

            if not test:
                raise Exception("Can't find test %s" % hash)

            logging.info("Trying to boot a deployment for %s", test.hash + "/" + test.testDefinitionSummary.name)

            cat = self._machineCategoryForTest(test)
            assert cat

            if cat.hardwareComboUnbootable:
                raise Exception("Can't boot %s, %s" % (cat.hardware, cat.os))

            deploymentId = self.database.Deployment.New(
                test=test,
                createdTimestamp=timestamp,
                isAlive=True
                )._identity

            cat.desired = cat.desired + 1

            self.streamForDeployment(deploymentId).addMessageFromDeployment(
                time.asctime() + " TestLooper> Deployment for %s waiting for hardware.\n\r" % (test.hash + "/" + test.testDefinitionSummary.name)
                )

            self._scheduleBootCheck()

            return deploymentId

    def subscribeToClientMessages(self, deploymentId, onTestOutput):
        self.streamForDeployment(deploymentId).subscribeToClientMessages(onTestOutput)

    def subscribeToDeployment(self, deploymentId, onTestOutput):
        self.streamForDeployment(deploymentId).subscribeToDeploymentMessages(onTestOutput)

    def unsubscribeFromDeployment(self, deploymentId, onTestOutput):
        self.streamForDeployment(deploymentId).unsubscribeFromDeploymentMessages(onTestOutput)

    def writeMessageToDeployment(self, deploymentId, msg):
        """msg: a TestLooperServer.TerminalInputMsg"""
        self.streamForDeployment(deploymentId).addMessageFromClient(msg)

    def totalRunningCountForCommit(self, commit):
        if not commit.data:
            return 0

        res = 0
        for test in commit.data.tests.values():
            res += max(0, test.activeRuns)

        return res

    def totalRunningCountForTest(self, test):
        return test.activeRuns

    def prioritizeAllCommitsUnderBranch(self, branch, depth, prioritize=True):
        commits = {}
        def check(c):
            if not c or c in commits or len(commits) >= depth:
                return

            commits[c] = True

            self._setCommitUserEnabledTestSets(c, ["all"] if prioritize else [])

            for r in self.database.CommitRelationship.lookupAll(child=c):
                check(r.parent)

        check(branch.head)
        
    def deprioritizeAllCommitsUnderBranch(self, branch, depth):
        self.prioritizeAllCommitsUnderBranch(branch, depth, prioritize=False)
        
    def toggleBranchUnderTest(self, branch):
        branch.isUnderTest = not branch.isUnderTest
        if branch.head and not branch.head.userEnabledTestSets and branch.isUnderTest:
            self._setCommitUserEnabledTestSets(branch.head, branch.head.data.triggeredTestSets)
        
    def getTestRunById(self, testIdentity):
        testIdentity = str(testIdentity)

        t = self.database.TestRun(testIdentity)
        if t.exists():
            return t

    def machineInitialized(self, machineId, curTimestamp):
        with self.transaction_and_lock():
            machine = self.database.Machine.lookupAny(machineId=machineId)
            if machine:
                self._machineHeartbeat(machine, curTimestamp)
            else:
                logging.warn("Initialization from unknown machine %s", machineId)


    def machineHeartbeat(self, machineId, curTimestamp, msg=None):
        if msg:
            logging.info("Machine %s heartbeating %s", machineId, msg)

        with self.transaction_and_lock():
            machine = self.database.Machine.lookupAny(machineId=machineId)
            if machine:
                self._machineHeartbeat(machine, curTimestamp, msg)
            else:
                logging.warn("Hearbeat from unknown machine %s", machineId)

    def _machineHeartbeat(self, machine, curTimestamp, msg=None):
        if machine.firstHeartbeat == 0.0:
            machine.firstHeartbeat = curTimestamp
        machine.lastHeartbeat=curTimestamp
        if msg:
            machine.lastHeartbeatMsg = msg
            
    def markRepoListDirty(self, curTimestamp):
        with self.transaction_and_lock():
            self._queueTask(
                self.database.DataTask.New(
                    task=self.database.BackgroundTask.RefreshRepos(), 
                    status=pendingVeryHigh
                    )
                )

    def markBranchListDirty(self, reponame, curTimestamp):
        with self.transaction_and_lock():
            repo = self.database.Repo.lookupAny(name=reponame)
            assert repo, "Can't find repo named %s" % reponame
            self._queueTask(
                self.database.DataTask.New(
                    task=self.database.BackgroundTask.RefreshBranches(repo=repo),
                    status=pendingVeryHigh
                    )
                )

    def _testNameSet(self, testNames):
        shaHash = sha_hash(tuple(sorted(testNames))).hexdigest
        cur = self.database.IndividualTestNameSet.lookupAny(shaHash=shaHash)
        if not cur:
            return self.database.IndividualTestNameSet.New(shaHash=shaHash, test_names=sorted(testNames))
        else:
            return cur


    def clearTestRun(self, testId):
        """Remove a test run from the database."""

        with self.transaction_and_lock():
            testRun = self.database.TestRun(str(testId))

            assert testRun.exists()

            if testRun.endTimestamp == 0.0:
                logging.info("Canceling testRun %s because user is canceling the test.", testRun._identity)
                self._cancelTestRun(testRun, time.time())
                return

            testRun.test.totalRuns = testRun.test.totalRuns - 1
            if testRun.success:
                testRun.test.successes = testRun.test.successes - 1

            self._addIndividualTestRecordsTo(
                testRun.test, 
                self._singleTestRecordsForRun(testRun),
                remove=True
                )

            testRun.canceled = True

            self._triggerTestPriorityUpdate(testRun.test)

    def _singleTestRecordsForRun(self, testRun):
        """Convert the compacted representation of tests we have in a TestRun back to a list of SingleTestRunResult"""
        _timesseen = {}
        def timesSeen(x):
            _timesseen[x] = _timesseen.get(x,-1) + 1
            return _timesseen[x]

        return [SingleTestRunResult.SingleTestRunResult(
                testName=testRun.testNames.test_names[testRun.testStepNameIndex[i]],
                startTimestamp=testRun.testStepTimeStarted[i],
                elapsed=testRun.testStepTimeElapsed[i],
                testSucceeded=testRun.testStepSucceeded[i],
                hasLogs=testRun.testStepHasLogs[i],
                testPassIx=timesSeen(testRun.testStepNameIndex[i])
                ) for i in xrange(len(testRun.testStepNameIndex))
                ]

    def _importTestRun(self, test, identity, startedTimestamp, lastHeartbeat, endTimestamp, success, 
                      canceled, testNameList, 
                      testStepNameIndex,
                      testStepTimeStarted,
                      testStepTimeElapsed,
                      testStepSucceeded,
                      testStepHasLogs,
                      testCount, failedTestCount):
        
        testRun = self.database.TestRun.New(
            _identity=identity,
            test=test,
            startedTimestamp=startedTimestamp,
            lastHeartbeat=lastHeartbeat,
            endTimestamp=endTimestamp,
            canceled=canceled,
            success=success,
            testNames=self._testNameSet(testNameList),
            testStepNameIndex=testStepNameIndex,
            testStepTimeStarted=testStepTimeStarted,
            testStepTimeElapsed=testStepTimeElapsed,
            testStepSucceeded=Bitstring.Bitstring(testStepSucceeded),
            testStepHasLogs=Bitstring.Bitstring(testStepHasLogs),
            totalTestCount=testCount,
            totalFailedTestCount=failedTestCount
            )

        self._addIndividualTestRecordsTo(
            testRun.test, 
            self._singleTestRecordsForRun(testRun)
            )

        if success:
            test.successes += 1

        if not canceled:
            if endTimestamp > 0.0:
                test.totalRuns += 1
            else:
                test.activeRuns += 1

        self._triggerTestPriorityUpdate(testRun.test)

    @staticmethod
    def configurationForTest(test):
        return test.testDefinitionSummary.configuration

    def recordTestArtifactUploaded(self, testId, artifact, curTimestamp, isCumulative):
        with self.transaction_and_lock():
            testRun = self.database.TestRun(str(testId))

            if not testRun.exists():
                logging.warn("Test run %s doesn't exist but we tried to register results for it.", testId)
                return

            if not isCumulative:
                artifactIx = len(testRun.artifactsCompleted)

                if artifactIx >= len(testRun.test.testDefinitionSummary.artifacts):
                    raise Exception("Unexpected artifact for test %s. We thought we were done, but got %s" 
                            % (testId, artifact))

                expected = testRun.test.testDefinitionSummary.artifacts[artifactIx]

                if artifact != expected:
                    raise Exception("Unexpected artifact for test %s. We expected ix=%s to be %s but got %s" % (
                        testId,
                        artifactIx,
                        expected,
                        artifact
                        ))

                testRun.artifactsCompleted = testRun.artifactsCompleted + (artifact,)
            else:
                if len(testRun.artifactsCompleted) > len(artifact) or \
                        list(artifact[:len(testRun.artifactsCompleted)]) != list(testRun.artifactsCompleted):
                    raise Exception("Unexpected cumulative artifact update for test %s: %s vs %s" % (
                        testId, artifact, testRun.artifactsCompleted
                        ))
                if len(artifact) > len(testRun.test.testDefinitionSummary.artifacts):
                    raise Exception("Unexpected cumulative artifact update for test %s: %s exceeds %s" % (
                        testId, artifact, testRun.test.testDefinitionSummary.artifacts
                        ))
                if list(artifact) != list(testRun.test.testDefinitionSummary.artifacts[:len(artifact)]):
                    raise Exception("Unexpected cumulative artifact update for test %s: %s vs %s" % (
                        testId, artifact, testRun.test.testDefinitionSummary.artifacts
                        ))

                testRun.artifactsCompleted = artifact

            for dep in self.database.TestDependency.lookupAll(dependsOn=testRun.test):
                self._updateTestPriority(dep.test, curTimestamp)


    def recordTestResults(self, success, testId, testSuccesses, artifacts, curTimestamp):
        """record test results for a run.

        testSuccesses - a list of SingleTestRunResult objects.
        """
        self.recordTestArtifactUploaded(testId, artifacts, curTimestamp, True)

        with self.transaction_and_lock():
            testRun = self.database.TestRun(str(testId))

            if not testRun.exists():
                return False

            if testRun.canceled:
                return False

            if success:
                if testRun.artifactsCompleted != testRun.test.testDefinitionSummary.artifacts:
                    logging.warn("Test %s didn't get the right list of completed artifacts: %s != %s", testId,
                        testRun.artifactsCompleted,
                        testRun.test.testDefinitionSummary.artifacts
                        )
                    assert False

            testRun.endTimestamp = curTimestamp
            
            testRun.test.activeRuns = testRun.test.activeRuns - 1
            testRun.test.totalRuns = testRun.test.totalRuns + 1
            testRun.test.lastTestEndTimestamp = curTimestamp

            names = sorted(set([s.testName for s in testSuccesses]))
            name_to_ix = {n:ix for ix,n in enumerate(names)}

            testRun.testNames = self._testNameSet(names)
            testRun.testStepNameIndex = [name_to_ix[s.testName] for s in testSuccesses]
            testRun.testStepTimeElapsed = [s.elapsed for s in testSuccesses]
            testRun.testStepTimeStarted = [s.startTimestamp for s in testSuccesses]
            testRun.testStepSucceeded = Bitstring.Bitstring.fromBools([s.testSucceeded for s in testSuccesses])
            testRun.testStepHasLogs = Bitstring.Bitstring.fromBools([s.hasLogs for s in testSuccesses])

            runs_by_name = {n:0 for n in names}
            failures_by_name = {n: 0 for n in names}

            for record in testSuccesses:
                runs_by_name[record.testName] += 1
                if not record.testSucceeded:
                    failures_by_name[record.testName] += 1

            avgFC = 0.0
            for name in runs_by_name:
                if runs_by_name[name] > 0:
                    avgFC += float(failures_by_name[name]) / float(runs_by_name[name])
            testRun.totalFailedTestCount = avgFC
            testRun.totalTestCount = len(names)

            self._addIndividualTestRecordsTo(testRun.test, testSuccesses)

            testRun.success = success
            self.heartbeatHandler.testFinished(testId)

            if success:
                testRun.test.successes = testRun.test.successes + 1

            testRun.machine.lastTestCompleted = curTimestamp

            os = testRun.machine.os

            if (os.matches.WindowsVM or os.matches.LinuxVM):
                #we need to shut down this machine since it has a setup script
                if not DISABLE_MACHINE_TERMINATION:
                    self._terminateMachine(testRun.machine, curTimestamp)

            for dep in self.database.TestDependency.lookupAll(dependsOn=testRun.test):
                self._updateTestPriority(dep.test, curTimestamp)

            self._updateTestPriority(testRun.test, curTimestamp)

            return True

    def _addIndividualTestRecordsTo(self, test, testRunResults, remove=False):
        self._setTestParentIfPossible(test)

        summary = test.testResultSummary

        self._expandTestNameSet(summary, testRunResults)

        name_to_ix = {n:ix for ix,n in enumerate(summary.testNames.test_names)}

        testTotalRuns = list(summary.testTotalRuns)

        runs = list(summary.testTotalRuns)
        failures = list(summary.testTotalFailures)
        hasLogs = list(summary.testHasLogs)
        
        for result in testRunResults:
            ix = name_to_ix[result.testName]
            runs[ix] += 1 if not remove else -1
            if not result.testSucceeded:
                failures[ix] += 1 if not remove else -1
            if result.hasLogs:
                hasLogs[ix] += 1

        avgFailureRate = 0.0
        totalTestCount = 0
        for ix in xrange(len(runs)):
            if runs[ix] > 0:
                avgFailureRate += float(failures[ix]) / float(runs[ix])
                totalTestCount += 1

        summary.testTotalRuns = runs
        summary.testTotalFailures = failures
        summary.testHasLogs = hasLogs
        summary.avgFailureRate = avgFailureRate
        summary.totalTestCount = totalTestCount

        self._recalculateTestSummaryStatistics(summary)

    def _classifyTestByIndex(self, summary, ix):
        failures, runs = summary.testTotalFailures[ix], summary.testTotalRuns[ix]

        if runs > 1 and failures > 0 and failures < runs:
            return 'flakey'
        elif failures == runs:
            return 'bad'
        else:
            return 'good'

    def _recalculateTestSummaryStatistics(self, summary):
        index_in_parent = {}

        testCount = len(summary.testNames.test_names)

        look_good = [False] * testCount
        look_bad = [False] * testCount
        look_flakey = [False] * testCount
        look_broken = [False] * testCount
        look_fixed = [False] * testCount
        look_new = [False] * testCount

        hasParentButNotRunYet = summary.test.parent and summary.test.parent.totalRuns == 0

        if summary.test.parent and summary.test.parent.testResultSummary.testNames:
            summary.removedTests = self._testNameSet(
                sorted(set(summary.test.parent.testResultSummary.testNames.test_names) 
                                - set(summary.testNames.test_names))
                )
            
            for ix, name in enumerate(summary.test.parent.testResultSummary.testNames.test_names):
                index_in_parent[name] = ix
        
        for ix, name in enumerate(summary.testNames.test_names):
            classify = self._classifyTestByIndex(summary, ix)

            if name in index_in_parent:
                parent_classify = self._classifyTestByIndex(summary.test.parent.testResultSummary, index_in_parent[name])

                if classify == 'good' and parent_classify == 'good':
                    look_good[ix] = True
                elif classify == 'bad' and parent_classify == 'bad':
                    look_bad[ix] = True
                elif classify == 'flakey':
                    look_flakey[ix] = True
                elif classify == 'good':
                    look_fixed[ix] = True
                elif classify == "bad":
                    look_broken[ix] = True
                else:
                    assert False, "should never happen"

            else:
                if classify == 'good':
                    look_good[ix] = True
                elif classify == 'bad':
                    look_bad[ix] = True
                elif classify == 'flakey':
                    look_flakey[ix] = True

                if not hasParentButNotRunYet:
                    look_new[ix] = True

        summary.testLooksGood = Bitstring.Bitstring.fromBools(look_good)
        summary.testLooksBad = Bitstring.Bitstring.fromBools(look_bad)
        summary.testLooksFlakey = Bitstring.Bitstring.fromBools(look_flakey)
        summary.testLooksBroken = Bitstring.Bitstring.fromBools(look_broken)
        summary.testLooksFixed = Bitstring.Bitstring.fromBools(look_fixed)
        summary.testLooksNew = Bitstring.Bitstring.fromBools(look_new)

        summary.testLooksGoodTotal = sum([1 if x else 0 for x in look_good])
        summary.testLooksBadTotal = sum([1 if x else 0 for x in look_bad])
        summary.testLooksFlakeyTotal = sum([1 if x else 0 for x in look_flakey])
        summary.testLooksBrokenTotal = sum([1 if x else 0 for x in look_broken])
        summary.testLooksFixedTotal = sum([1 if x else 0 for x in look_fixed])
        summary.testLooksNewTotal = sum([1 if x else 0 for x in look_new])

    def _expandTestNameSet(self, summary, testRunResults):
        existingNamesSet = set()
        existingNames = []
        if summary.testNames:
            existingNames = summary.testNames.test_names
            existingNamesSet = set(existingNames)

        allNames = sorted(set([t.testName for t in testRunResults]))
        new_names = []
        for n in sorted(allNames):
            if n not in existingNamesSet:
                new_names.append(n)

        if new_names or not summary.testNames:
            summary.testNames = self._testNameSet(existingNames + new_names)
            summary.testTotalRuns = summary.testTotalRuns + (0,) * len(new_names)
            summary.testTotalFailures = summary.testTotalRuns + (0,) * len(new_names)
            summary.testHasLogs = summary.testTotalRuns + (0,) * len(new_names)

    def handleTestConnectionReinitialized(self, testId, timestamp, allLogs, allArtifacts):
        self.heartbeatHandler.testHeartbeatReinitialized(testId, timestamp, allLogs)
        self.recordTestArtifactUploaded(testId, allArtifacts, timestamp, isCumulative=True)
        return self.testHeartbeat(testId, timestamp)

    def testHeartbeat(self, testId, timestamp, logMessage = None):
        if not self.heartbeatHandler.testHeartbeat(testId, timestamp, logMessage):
            return True

        logging.debug('test %s heartbeating', testId)

        with self.transaction_and_lock():
            testRun = self.database.TestRun(str(testId))

            if not testRun.exists():
                return False

            if testRun.canceled:
                return False

            if not testRun.machine.isAlive:
                logging.error("Test %s heartbeat, but machine %s is dead! Canceling test.", testId, testRun.machine.machineId)

                self._cancelTestRun(testRun, timestamp)
                return False
            else:
                self._machineHeartbeat(testRun.machine, timestamp)

                testRun.lastHeartbeat = timestamp

                return True

    def checkAllTestPriorities(self, curTimestamp, resetUnbootable):
        with self.transaction_and_lock():
            self._checkAllTestPriorities(curTimestamp, resetUnbootable)

    def _commitMightHaveTests(self, c):
        return c.data and c.data.timestamp > OLDEST_TIMESTAMP_WITH_TESTS        

    def _allCommitsWithPossibilityOfTests(self):
        commits = set()
        to_check = set()

        for repo in self.database.Repo.lookupAll(isActive=True):
            for branch in self.database.Branch.lookupAll(repo=repo):
                if branch.head:
                    to_check.add(branch.head)

        while to_check:
            c = to_check.pop()
            if c not in commits:
                commits.add(c)
                if c.data and self._commitMightHaveTests(c):
                    for child in c.data.parents:
                        if child not in commits:
                            to_check.add(child)

        return commits

    def _checkActiveRunsLooksCorrupt(self):
        for priorityType in [
                self.database.TestPriority.FirstBuild,
                self.database.TestPriority.FirstTest,
                self.database.TestPriority.WantsMoreTests
                ]:
            for priority in reversed(range(1,MAX_TEST_PRIORITY+1)):
                for test in self.database.Test.lookupAll(priority=priorityType(priority)):
                    if test.activeRuns < 0:
                        return True
        return False

    def _checkAllTestPriorities(self, curTimestamp, resetUnbootable):
        logging.info("Checking all test priorities to ensure they are correct")

        total = 0

        if resetUnbootable:
            categories = set()
    
            commitsWithTests = self._allCommitsWithPossibilityOfTests()

            for c in commitsWithTests:
                for test in self.allTestsForCommit(c):
                    categories.add(test.machineCategory)

            changed = set()
            for category in categories:
                if category.hardwareComboUnbootable:
                    logging.info("Category %s/%s marked unbootable because %s", category.hardware, category.os, category.hardwareComboUnbootableReason)
                    category.hardwareComboUnbootable = False
                    changed.add(category)

            for c in commitsWithTests:
                for test in self.allTestsForCommit(c):
                    if test.machineCategory in changed and test.priority.matches.HardwareComboUnbootable:
                        logging.info("Updating mispriorizited test %s", test)
                        self._updateTestPriority(test, curTimestamp)


        if self._checkActiveRunsLooksCorrupt():
            logging.warn("Active runs looks corrupt. Rebuilding.")
            commitsWithTests = self._allCommitsWithPossibilityOfTests()

            for c in commitsWithTests:
                for test in self.allTestsForCommit(c):
                    test.activeRuns = 0


            for runningTest in self.database.TestRun.lookupAll(isRunning=True):
                runningTest.test.activeRuns += 1

    
        for priorityType in [
                self.database.TestPriority.FirstBuild,
                self.database.TestPriority.FirstTest,
                self.database.TestPriority.WantsMoreTests
                ]:
            for priority in reversed(range(1,MAX_TEST_PRIORITY+1)):
                for test in self.database.Test.lookupAll(priority=priorityType(priority)):
                    total += 1
                    self._updateTestPriority(test, curTimestamp)

        logging.info("Done checking all test priorities to ensure they are correct. Checked %s", total)


                    
    def _lookupHighestPriorityTest(self, machine, curTimestamp):
        t0 = time.time()

        count = 0

        for priorityType in [
                self.database.TestPriority.FirstBuild,
                self.database.TestPriority.FirstTest,
                self.database.TestPriority.WantsMoreTests
                ]:
            for priorityLevel in reversed(range(1,MAX_TEST_PRIORITY+1)):
                priority=priorityType(priorityLevel)

                for test in self.database.Test.lookupAll(priority=priority):
                    if self._machineCategoryPairForTest(test) == (machine.hardware, machine.os):
                        return test

    def startNewDeployment(self, machineId, timestamp):
        """Allocates a new test and returns (deploymentId, testDefinition) or (None,None) if no work."""
        with self.transaction_and_lock():
            machine = self.database.Machine.lookupAny(machineId=machineId)

            if machine is None or not machine.isAlive:
                logging.warn("Can't assign work to a machine we don't know about: %s", machineId)
                return None, None

            self._machineHeartbeat(machine, timestamp)

            for deployment in self.database.Deployment.lookupAll(isAliveAndPending=True):
                if self._machineCategoryForTest(deployment.test) == self._machineCategoryForPair(machine.hardware, machine.os):
                    deployment.machine = machine
                    
                    self.streamForDeployment(deployment._identity).addMessageFromDeployment(
                        time.asctime(time.gmtime(timestamp)) + 
                            " TestLooper> Machine %s accepting deployment.\n\r" % machineId
                        )
                    
                    test = deployment.test

                    return (deployment._identity, self.definitionForTest(test))

            return None, None

    def isDeployment(self, deploymentId):
        with self.database.view():
            return self.database.Deployment(deploymentId).exists()


    def handleDeploymentConnectionReinitialized(self, deploymentId, timestamp, allLogs):
        self.streamForDeployment(deploymentId).allMessagesFromDeploymentFromStart(allLogs)

        return self.handleMessageFromDeployment(deploymentId, timestamp, "")

    def handleMessageFromDeployment(self, deploymentId, timestamp, msg):
        with self.transaction_and_lock():
            deployment = self.database.Deployment(deploymentId)

            if deployment.machine:
                deployment.machine.lastHeartbeat = timestamp

            if not deployment.exists() or not deployment.isAlive:
                return False

        self.streamForDeployment(deploymentId).addMessageFromDeployment(msg)

        return True

    def startNewTest(self, machineId, timestamp):
        """Allocates a new test and returns (testId, testDefinition) or (None,None) if no work."""
        with self.transaction_and_lock():
            machine = self.database.Machine.lookupAny(machineId=machineId)

            if not machine or not machine.isAlive:
                return None, None, None

            self._machineHeartbeat(machine, timestamp)

        with self.transaction_and_lock():
            t0 = time.time()
            test = self._lookupHighestPriorityTest(machine, timestamp)
            if time.time() - t0 > .25:
                logging.warn("Took %s to get priority", time.time() - t0)

            if not test:
                return None, None, None

            test.activeRuns = test.activeRuns + 1

            machine = self.database.Machine.lookupOne(machineId=machineId)

            runningTest = self.database.TestRun.New(
                test=test,
                startedTimestamp=timestamp,
                lastHeartbeat=timestamp,
                machine=machine
                )

            self._updateTestPriority(test, timestamp)

            return (runningTest._identity, self.definitionForTest(test), self.historicalTestFailureRate(test))

    def historicalTestFailureRate(self, test, maxCount=10):
        """look back over test history and assemble test failure rates. try to get 'maxCount' runs
        per test. Exit after maxCount prior tests have been seen."""
        result = {}

        lookback = 0
        while lookback < maxCount and test:
            self._setTestParentIfPossible(test)

            if test.testResultSummary.testNames:
                for ix, name in enumerate(test.testResultSummary.testNames.test_names):
                    failures, totalRuns = result.get(name,(0,0))
                    if totalRuns < maxCount:
                        totalRuns += test.testResultSummary.testTotalRuns[ix]
                        failures += test.testResultSummary.testTotalFailures[ix]
                        result[name] = (failures,totalRuns)
            test = test.parent
            lookback += 1

        return result

    def performCleanupTasks(self, curTimestamp):
        #check all tests to see if we've exceeded the timeout and the test is dead
        with self.transaction_and_lock():
            for t in self.database.TestRun.lookupAll(isRunning=True):
                if t.lastHeartbeat < curTimestamp - TEST_TIMEOUT_SECONDS and curTimestamp - self.initialTimestamp > TEST_TIMEOUT_SECONDS:
                    logging.error("Canceling testRun %s because it has not had a heartbeat for a long time. Most recent logs:\n%s", 
                        t._identity,
                        self.heartbeatHandler.getMostRecentTestHeartbeats(t._identity)
                        )
                    
                    self._cancelTestRun(t, curTimestamp)

            for m in self.database.Machine.lookupAll(isAlive=True):
                heartbeat = max(m.lastHeartbeat, m.bootTime)

                if m.lastHeartbeat == 0:
                    timeout = MACHINE_TIMEOUT_SECONDS_FIRST_HEARTBEAT
                else:
                    timeout = MACHINE_TIMEOUT_SECONDS

                if heartbeat < curTimestamp - timeout and \
                        curTimestamp - self.initialTimestamp > MACHINE_TIMEOUT_SECONDS:
                    logging.info("Shutting down machine %s because it has not heartbeat in a long time",
                        m.machineId
                        )
                    self._terminateMachine(m, curTimestamp)

        with self.transaction_and_lock():
            self._scheduleBootCheck()
            self._shutdownMachinesIfNecessary(curTimestamp)
            self._checkRetryTests(curTimestamp)
            
    def _checkRetryTests(self, curTimestamp):
        for test in self.database.Test.lookupAll(waiting_to_retry=True):
            self._triggerTestPriorityUpdate(test)

    def _scheduleBootCheck(self):
        if not self.database.DataTask.lookupAny(pending_boot_machine_check=True):
            self._queueTask(
                self.database.DataTask.New(
                    task=self.database.BackgroundTask.BootMachineCheck(),
                    status=pendingVeryHigh
                    )
                )

    def _cancelTestRun(self, testRun, curTimestamp):
        assert testRun.endTimestamp == 0.0

        if testRun.canceled:
            return

        testRun.canceled = True

        testRun.test.activeRuns = testRun.test.activeRuns - 1
        self.heartbeatHandler.testFinished(testRun.test._identity)


        if testRun.machine:
            os = testRun.machine.os

            if (os.matches.WindowsVM or os.matches.LinuxVM):
                #we need to shut down this machine since it has a setup script
                if not DISABLE_MACHINE_TERMINATION:
                    self._terminateMachine(testRun.machine, curTimestamp)

        self._triggerTestPriorityUpdate(testRun.test)

    def _taskCount(self):
        count = 0
        for task in (self.database.DataTask.lookupAll(status=pendingVeryHigh) +
                        self.database.DataTask.lookupAll(status=pendingHigh) +
                        self.database.DataTask.lookupAll(status=pendingMedium) +
                    self.database.DataTask.lookupAll(status=pendingLow)):
            count += task.prior_ct
        return count

    def performBackgroundWorkSynchronously(self, curTimestamp, count):
        with self.transaction_and_lock() as curLock:
            logging.info("Tasks pending: %s", self._taskCount())
            logging.info("Total commits: %s",
                sum([r.commits for r in  self.database.Repo.lookupAll(isActive=True)])
                )

            for i in xrange(count):
                task = self.database.DataTask.lookupAny(status=pendingVeryHigh)
                if task is None:
                    task = self.database.DataTask.lookupAny(status=pendingHigh)
                if task is None:
                    task = self.database.DataTask.lookupAny(status=pendingMedium)
                if task is None:
                    task = self.database.DataTask.lookupAny(status=pendingLow)
                if task is None:
                    task = self.database.DataTask.lookupAny(status=pendingVeryLow)

                if task is None:
                    return

                testDef = task.task
                task.status = running

                try:
                    self._processTask(task.task, curTimestamp, curLock)
                except KeyboardInterrupt:
                    raise
                except:
                    traceback.print_exc()
                    logging.error("Exception processing task %s:\n\n%s", task.task, traceback.format_exc())
                finally:
                    if task.prior:
                        task.prior.isHead = True
                    task.delete()

        return testDef

    def touchAllTestsAndRuns(self, curTimestamp):
        toCheck = set()
        commits = set()

        with self.transaction_and_lock():

            for repo in self.database.Repo.lookupAll(isActive=True):
                for branch in self.database.Branch.lookupAll(repo=repo):
                    if branch.head:
                        toCheck.add(branch.head)

        while toCheck:
            c = toCheck.pop()
            if c not in commits:
                if len(commits) % 1000 == 0:
                    logging.info("Have done %s commits. %s pending", len(commits), len(toCheck))
                commits.add(c)

                with self.transaction_and_lock():
                    if c.data:
                        for p in c.data.parents:
                            toCheck.add(p)

                        commitTestDefs = None

                        for test in c.data.tests.values():
                            for typename in test.__types__:
                                getattr(test, typename)

                            for run in self.database.TestRun.lookupAll(test=test):
                                for typename in run.__types__:
                                    getattr(run, typename)

    def performBackgroundWork(self, curTimestamp):
        with self.transaction_and_lock():
            task = self.database.DataTask.lookupAny(status=pendingVeryHigh)
            if task is None:
                task = self.database.DataTask.lookupAny(status=pendingHigh)
            if task is None:
                task = self.database.DataTask.lookupAny(status=pendingMedium)
            if task is None:
                task = self.database.DataTask.lookupAny(status=pendingLow)
            if task is None:
                task = self.database.DataTask.lookupAny(status=pendingVeryLow)

            if task is None:
                return
                
            task.status = running

            testDef = task.task

        try:
            with self.transaction_and_lock() as curLock:
                self._processTask(testDef, curTimestamp, curLock)
        except KeyboardInterrupt:
            raise
        except:
            traceback.print_exc()
            logging.error("Exception processing task %s:\n\n%s", testDef, traceback.format_exc())
        finally:
            with self.transaction_and_lock():
                if task.prior:
                    task.prior.isHead=True
                task.delete()

        return testDef

    def _machineTerminated(self, machineId, timestamp):
        machine = self.database.Machine.lookupOne(machineId=machineId)

        if not machine.isAlive:
            return

        machine.isAlive = False
        mc = self._machineCategoryForPair(machine.hardware, machine.os)
        
        mc.booted = mc.booted - 1

        assert mc.booted >= 0

        for testRun in list(self.database.TestRun.lookupAll(runningOnMachine=machine)):
            logging.info("Canceling testRun %s because the machine it's on is terminated.", testRun._identity)
            self._cancelTestRun(testRun, timestamp)

        for deployment in list(self.database.Deployment.lookupAll(runningOnMachine=machine)):
            self._cancelDeployment(deployment, timestamp)

        self._scheduleBootCheck()

    def pruneDeadWorkers(self, curTimestamp):
        with self.transaction_and_lock():
            self._pruneDeadWorkers(curTimestamp)

    def _pruneDeadWorkers(self, curTimestamp):
        self.lastWorkerPruneOperation = curTimestamp

        self._checkMachineCategoryCounts()
            
        known_workers = {x.machineId: (x.hardware, x.os) for x in self.database.Machine.lookupAll(isAlive=True)}

        to_kill = self.machine_management.synchronize_workers(known_workers)

        for machineId in to_kill:
            if machineId in known_workers:
                logging.info("Worker %s is unknown to machine management. Removing it.", machineId)
                self._machineTerminated(machineId, curTimestamp)

    def getRawTestFileForCommit(self, commit):
        if not commit.data:
            return None

        repo = self.source_control.getRepo(commit.repo.name)
        
        defText, extension = repo.getTestScriptDefinitionsForCommit(commit.hash)

        return defText, extension

    def _processTask(self, task, curTimestamp, curLock):
        if task.matches.RefreshRepos:
            self._refreshRepos()
        elif task.matches.RefreshBranches:
            self._refreshBranches(task.repo, curTimestamp, curLock)
        elif task.matches.UpdateBranchPins:
            branch = task.branch

            if not (branch.head and branch.head.data):
                return

            pinning = BranchPinning.BranchPinning(self.database, self.source_control)
            pinning.updateBranchPin(branch, lookDownstream=True)

            for branch_updated in pinning.branches_updated:
                self._scheduleUpdateBranchTopCommit(branch_updated)

        elif task.matches.UpdateBranchTopCommit:
            self._updateBranchTopCommit(task.branch)
        elif task.matches.CommitTestParse:
            self._parseCommitTests(task.commit)
        elif task.matches.UpdateCommitData:
            self._updateCommitData(task.commit)

        elif task.matches.UpdateTestPriority:
            self._updateTestPriority(task.test, curTimestamp)
        elif task.matches.BootMachineCheck:
            self._bootMachinesIfNecessary(curTimestamp, curLock)
        elif task.matches.CheckBranchAutocreate:
            self._checkBranchAutocreate(task.branch, curTimestamp)
        elif task.matches.UpdateCommitPriority:
            self._updateCommitPriority(task.commit)
        else:
            raise Exception("Unknown task: %s" % task)

    def _checkBranchAutocreate(self, branch, timestamp):
        if branch.autocreateTrackingBranchName:
            return

        for template in branch.repo.branchCreateTemplates:
            if self._branchMatchesTemplate(branch, template):
                try:
                    logMessage = self._createNewBranchFromTemplate(branch, template)
                except:
                    logMessage = "Error creating branch from template:\n" + traceback.format_exc()

                branch.repo.branchCreateLogs = self._createLogMessage(branch.repo.branchCreateLogs, logMessage, timestamp)

                return

        if branch.repo.branchCreateTemplates:
            logMessage = "New branch %s didn't match any templates." % branch.branchname

            branch.repo.branchCreateLogs = self._createLogMessage(branch.repo.branchCreateLogs, logMessage, timestamp)

    def _createLogMessage(self, priorLog, text, timestamp):
        if not priorLog:
            priorLog = self.database.LogMessage.Null
        return self.database.LogMessage.New(msg=text, timestamp=timestamp,prior=priorLog)

    def _branchMatchesTemplate(self, branch, template):
        if branch.branchname.startswith("svn-"):
            return False

        if not template.suffix:
            return False

        if branch.branchname.endswith(template.suffix):
            return False

        for exclude in template.globsToExclude:
            if fnmatch.fnmatchcase(branch.branchname, exclude):
                return False
        for include in template.globsToInclude:
            if fnmatch.fnmatchcase(branch.branchname, include):
                return True
        return False

    def _createNewBranchFromTemplate(self, branch, template):
        source_branch = self.database.Branch.lookupAny(reponame_and_branchname=(branch.repo.name, template.branchToCopyFrom))
        if not source_branch:
            return "Source branch '%s' doesn't exist" % template.branchToCopyFrom

        if not template.suffix:
            return "Template branchname suffix is empty"

        new_name = branch.branchname + template.suffix
        if self.database.Branch.lookupAny(reponame_and_branchname=(branch.repo.name, new_name)):
            return "Newly created branch %s already exists." % new_name

        if not source_branch.head.data:
            return "Source branch didn't have a valid commit on its HEAD"
        
        if template.def_to_replace not in source_branch.head.data.repos:
            return "Couldn't find repodef %s amongst:\n%s" % (
                template.def_to_replace,
                "\n".join(["  " + x for x in sorted(source_branch.head.data.repos)])
                )

        if not source_branch.head.data.repos[template.def_to_replace].matches.Pin:
            return "Targeted reporef is not updatable."

        newRepoDefs = dict({k:v for k,v in source_branch.head.data.repos.iteritems() if v.matches.Pin})
        
        if template.disableOtherAutos:
            for defname in list(newRepoDefs):
                pin = newRepoDefs[defname]
                if pin.matches.Pin and defname != template.def_to_replace:
                    pin = pin._withReplacement(auto=False)
                newRepoDefs[defname] = pin

        newRepoDefs[template.def_to_replace] = (
            newRepoDefs[template.def_to_replace]
                ._withReplacement(auto=True)
                ._withReplacement(branch=branch.branchname)
                ._withReplacement(reference="%s/%s" % (branch.repo.name, "HEAD"))
            )

        pinning = BranchPinning.BranchPinning(self.database, self.source_control)
        
        try:
            newHash = pinning._updatePinsByDefInCommitAndReturnHash(
                source_branch,
                source_branch.head.data.repos,
                newRepoDefs, 
                "Pointing branch %s at %s for testing." % (source_branch.branchname, branch.branchname)
                )
        except Exception as e:
            return "Failed to update yaml file:\n%s" % traceback.format_exc()

        repo = self.source_control.getRepo(branch.repo.name)

        if not repo.source_repo.pushCommit(newHash, new_name, createBranch=True):
            return "Failed to push new commit to source control"

        #create an explicit new branch object
        newbranch = self.database.Branch.New(branchname=new_name, repo=branch.repo)

        if template.autoprioritizeBranch:
            newbranch.isUnderTest = True

        if template.deleteOnUnderlyingRemoval:
            newbranch.autocreateTrackingBranchName = branch.branchname

        self._repoTouched(newbranch.repo)
        self._scheduleUpdateBranchTopCommit(newbranch)

        return "Successfully pushed %s to new branch %s" % (newHash, new_name)

    def _refreshRepos(self):
        all_repos = set(self.source_control.listRepos())

        repos = self.database.Repo.lookupAll(isActive=True)

        for r in repos:
            if r.name not in all_repos:
                r.isActive = False

        existing = set([x.name for x in repos])

        for new_repo_name in all_repos - existing:
            r = self.database.Repo.lookupAny(name=new_repo_name)
            if r:
                r.isActive = True
            else:
                r = self._createRepo(new_repo_name)

        for r in self.database.Repo.lookupAll(isActive=True):
            self._queueTask(
                self.database.DataTask.New(
                    task=self.database.BackgroundTask.RefreshBranches(r),
                    status=pendingVeryLow
                    )
                )

    def _refreshBranches(self, db_repo, curTimestamp, curLock):
        repo = self.source_control.getRepo(db_repo.name)
        reponame = db_repo.name

        if curLock:
            #temporarily release the lock, since all we're doing is
            #interacting with the git server
            curLock.__exit__(None, None, None)
        
        try:
            if not self.source_control.isWebhookInstalled(reponame, self.server_port_config):
                self.source_control.installWebhook(
                    reponame,
                    self.server_port_config
                    )
        except:
            logging.error("Tried to install webhook for %s but failed: %s", 
                db_repo.name,
                traceback.format_exc()
                )

        repo.source_repo.fetchOrigin()

        if curLock:
            #reaquire the lock now that we've called 'git fetch'
            curLock.__enter__()

        branchnamesAndHashes = repo.source_repo.listBranchesForRemote("origin")

        branchnames_set = set(branchnamesAndHashes)

        db_branches = self.database.Branch.lookupAll(repo=db_repo)

        logging.info(
            "Comparing branchlist from server: %s to local: %s", 
            sorted(branchnames_set), 
            sorted([x.branchname for x in db_branches])
            )

        final_branches = tuple([x for x in db_branches if x.branchname in branchnames_set])
        for branch in db_branches:
            if branch.branchname not in branchnames_set:
                self._branchDeleted(branch, curTimestamp)
                branch.delete()

        for newname in branchnames_set - set([x.branchname for x in db_branches]):
            newbranch = self.database.Branch.New(branchname=newname, repo=db_repo)
            self._repoTouched(db_repo)

            self._queueTask(
                self.database.DataTask.New(
                    task=self.database.BackgroundTask.CheckBranchAutocreate(newbranch),
                    status=pendingLow
                    )
                )


        for branchname, branchHash in branchnamesAndHashes.iteritems():
            try:
                branch = self.database.Branch.lookupOne(reponame_and_branchname=(db_repo.name, branchname))
                if not branch.head or branch.head.hash != branchHash:
                    logging.info("Branch head %s looks dirty (%s != %s). Updating. ", 
                        branch.repo.name + "/" + branch.branchname, 
                        branch.head.hash if branch.head else "<none>",
                        branchHash
                        )
                    self._scheduleUpdateBranchTopCommit(branch)
            except:
                logging.error("Error scheduling branch commit lookup:\n\n%s", traceback.format_exc())

    def _updateCommitData(self, commit):
        commitCount = sum([repo.commits for repo in self.database.Repo.lookupAll(isActive=True)])

        logging.info("Updating commit data for %s/%s. We have %s commits total.", commit.repo.name, commit.hash, commitCount)
        
        source_control_repo = self.source_control.getRepo(commit.repo.name)

        if commit.data is self.database.CommitData.Null:
            if not source_control_repo.source_repo.commitExists(commit.hash):
                return

            for hashParentsAndTitle in source_control_repo.commitsLookingBack(commit.hash, 10):
                self._updateCommitDataForHash(
                    repo=commit.repo,
                    hash=hashParentsAndTitle[0],
                    timestamp=int(hashParentsAndTitle[2]),
                    subject=hashParentsAndTitle[3].split("\n")[0],
                    body=hashParentsAndTitle[3],
                    author=hashParentsAndTitle[4],
                    authorEmail=hashParentsAndTitle[5],
                    parentHashes=hashParentsAndTitle[1]
                    )

    def _updateSingleCommitData(self, commit, knownNoTestFile=False, commitInfoCache=None):
        source_control_repo = self.source_control.getRepo(commit.repo.name)

        if commit.data is self.database.CommitData.Null:
            if not source_control_repo.source_repo.commitExists(commit.hash):
                return

            if commitInfoCache is not None:
                if commit.hash not in commitInfoCache:
                    for hashParentsAndTitle in source_control_repo.commitsLookingBack(commit.hash, 50):
                        if hashParentsAndTitle[0] not in commitInfoCache:
                            commitInfoCache[hashParentsAndTitle[0]] = hashParentsAndTitle

                hashParentsAndTitle = commitInfoCache[commit.hash]
            else:
                hashParentsAndTitle = source_control_repo.source_repo.gitCommitData(commit.hash)

            self._updateCommitDataForHash(
                repo=commit.repo,
                hash=hashParentsAndTitle[0],
                timestamp=int(hashParentsAndTitle[2]),
                subject=hashParentsAndTitle[3].split("\n")[0],
                body=hashParentsAndTitle[3],
                author=hashParentsAndTitle[4],
                authorEmail=hashParentsAndTitle[5],
                parentHashes=hashParentsAndTitle[1],
                knownNoTestFile=knownNoTestFile
                )

    def _forceTriggerCommitTestParse(self, commit):
        commit.data.testsParsed=False
        commit.data.noTestsFound=False
        self._parseCommitTests(commit)

        for branch in self.database.Branch.lookupAll(head=commit):
            self._recalculateBranchPins(branch)

    def _updateCommitDataForHash(self, repo, hash, timestamp, subject, body, author, authorEmail, parentHashes, knownNoTestFile=False):
        source_control_repo = self.source_control.getRepo(repo.name)

        commit = self._lookupCommitByHash(repo, hash)

        if commit is None or commit.data:
            logging.info("Not updating commit %s because it has commit.data and doesn't want a refresh.", commit.hash)
            return

        parents=[self._lookupCommitByHash(commit.repo, p) for p in parentHashes]

        self._repoTouched(repo)

        commit.data = self.database.CommitData.New(
            commit=commit,
            subject=subject,
            timestamp=timestamp,
            commitMessage=body,
            parents=parents,
            author=author,
            authorEmail=authorEmail
            )

        #check for all source dependencies and make sure we update them!
        for dep in self.database.UnresolvedCommitSourceDependency.lookupAll(repo_and_hash=(repo, hash)):
            commitNeedingUs = dep.commit
            dep.delete()
            self._triggerCommitTestParse(commitNeedingUs)

        for p in parents:
            self.database.CommitRelationship.New(child=commit,parent=p)
        
        #when we get new commits, make sure we have the right priority
        #on them. This is a one-time operation when the commit is first created
        #to apply the branch priority to the commit
        priority = 0

        for branch in self.database.Branch.lookupAll(head=commit):
            if branch.isUnderTest:
                priority = max(priority, 1)

        #if this is an auto-commit, only prioritize if the commit has been tagged
        pinUpdate = BranchPinning.unpackCommitPinUpdateMessage(commit.data.commitMessage)
        if pinUpdate and len(parents) == 1 and parents[0].data:
            #the unpacker returns the name of the reference (which we can look up)
            #in the last slot.
            refname_updated = pinUpdate[3]

            ref = parents[0].data.repos.get(refname_updated)

        if knownNoTestFile:
            commit.data.noTestsFound = True

        #ignore commits produced before the looper existed. They won't have these files, and it's
        #slow to import them
        elif commit.data.timestamp > OLDEST_TIMESTAMP_WITH_TESTS:
            logging.info("Loading data for commit %s with timestamp %s", commit.hash, time.asctime(time.gmtime(commit.data.timestamp)))
            self._triggerCommitPriorityUpdate(commit)

            self._parseCommitTests(commit)
        else:
            logging.info("Not loading data for commit %s with timestamp %s", commit.hash, time.asctime(time.gmtime(commit.data.timestamp)))

            commit.data.noTestsFound = True
            commit.data.testDefinitionsError = "Commit old enough that we won't check for test definitions."

        if priority:
            commit.userEnabledTestSets = commit.data.triggeredTestSets

        for branch in self.database.Branch.lookupAll(head=commit):
            self._recalculateBranchPins(branch)

    def _extractCommitTestsEnvsAndRepos(self, commit):
        raw_text, extension = self.getRawTestFileForCommit(commit)

        def getRepo(reponame):
            if not self.database.Repo.lookupAny(name=reponame):
                return None
            return self.source_control.getRepo(reponame).source_repo

        resolver = TestDefinitionResolver.TestDefinitionResolver(getRepo)

        return resolver.testEnvironmentAndRepoDefinitionsFor(
            commit.repo.name, 
            commit.hash
            )

    def testsForCommit(self, commit):
        if commit in self.commitTestCache_:
            return self.commitTestCache_[commit]

        raw_text, extension = self.getRawTestFileForCommit(commit)

        def getRepo(reponame):
            if not self.database.Repo.lookupAny(name=reponame):
                return None
            return self.source_control.getRepo(reponame).source_repo

        resolver = TestDefinitionResolver.TestDefinitionResolver(getRepo)

        tests = resolver.testEnvironmentAndRepoDefinitionsFor(
            commit.repo.name, 
            commit.hash
            )[0]

        self.commitTestCache_[commit] = tests

        if len(self.commitTestCache_) > 1000:
            self.commitTestCache_ = {}

        return tests


    def environmentForTest(self, test):
        return self.definitionForTest(test).environment

    def definitionForTest(self, test):
        """Extracts the testDefinition for a given test."""
        commitDep = self.database.CommitTestDependency.lookupAny(test=test)
        
        assert commitDep is not None, "Somehow, don't have a commit referencing %s" % test._identity

        return self.testsForCommit(commitDep.commit).get(test.testDefinitionSummary.name)

    def _parseCommitTests(self, commit):
        try:
            if commit.data.testsParsed:
                return

            raw_text, extension = self.getRawTestFileForCommit(commit)

            if not raw_text:
                commit.data.noTestsFound = True
                commit.data.testsParsed = True
                logging.info("No test data for %s", commit.hash)
                return
            else:
                logging.info("Test data found for %s", commit.hash)

            def getRepo(reponame):
                if not self.database.Repo.lookupAny(name=reponame):
                    return None
                return self.source_control.getRepo(reponame).source_repo

            resolver = TestDefinitionResolver.TestDefinitionResolver(getRepo)

            #make sure we at least get the repo pins if they're available.
            #otherwise, if we have a bad test we won't be able to roll repo pins forward
            commit.data.repos = resolver.unprocessedRepoPinsFor(commit.repo.name, commit.hash)
            
            try:
                all_tests, all_environments, all_repo_defs = \
                    resolver.testEnvironmentAndRepoDefinitionsFor(
                        commit.repo.name, 
                        commit.hash
                        )

            except TestDefinitionResolver.MissingDependencyException as e:
                self._createSourceDep(commit, e.reponame, e.commitHash)
                return

            commit.data.repos = all_repo_defs

            testSets, testSetsTopLevel, triggeredTestSets, triggeredTriggers = resolver.testSetsFor(commit.repo.name, commit.hash)

            commit.data.testSets = testSets
            commit.data.testSetsTopLevel = testSetsTopLevel
            commit.data.triggeredTestSets = triggeredTestSets
            commit.data.triggeredTriggers = triggeredTriggers
            
            tests_by_name = {}

            for e in all_tests.values():
                tests_by_name[e.name] = \
                    self._createTest(
                        commit=commit,
                        testDefinition=e
                        )

            commit.data.tests = tests_by_name

            commit.repo.commitsWithTests = commit.repo.commitsWithTests + 1

            commit.data.testsParsed = True

        except Exception as e:
            if not str(e):
                logging.error("%s", traceback.format_exc())

            logging.warn("Got an error parsing tests for %s/%s:\n%s", commit.repo.name, commit.hash, traceback.format_exc())

            commit.data.testDefinitionsError=traceback.format_exc(e)

            commit.data.testsParsed = True

    def _updateBranchPin(self, branch, ref_name, produceIntermediateCommits):
        pinning = BranchPinning.BranchPinning(self.database, self.source_control)
        pinning.updateBranchPin(branch, specific_ref=ref_name, intermediateCommits=produceIntermediateCommits, lookDownstream=False)

        for branch_updated in pinning.branches_updated:
            self._scheduleUpdateBranchTopCommit(branch_updated)

    def _branchDeleted(self, branch, curTimestamp):
        old_branch_head = branch.head
        if old_branch_head:
            self._setBranchHead(branch, self.database.Commit.Null)
            self._triggerCommitPriorityUpdate(old_branch_head)

        for trackingBranch in self.database.Branch.lookupAll(autocreateTrackingBranchName=branch.branchname):
            logging.info("Deleting test-tracking branch %s because %s was deleted." % (trackingBranch.branchname, branch.branchname))
            try:
                repo = self.source_control.getRepo(trackingBranch.repo.name).source_repo
                repo.deleteRemoteBranch(trackingBranch.branchname)
                trackingBranch.repo.branchCreateLogs = self._createLogMessage(
                    trackingBranch.repo.branchCreateLogs, 
                    "Deleted branch %s because underlying branch %s was deleted" % (
                        trackingBranch.branchname,
                        branch.branchname
                        ), 
                    curTimestamp
                    )
            except:
                logging.error("Failed to delete remote branch %s:\n\n%s", trackingBranch.branchname, traceback.format_exc())

    def _setBranchHead(self, branch, newHead):
        if branch:
            branch.head = newHead

            self._repoTouched(branch.repo)

    def _repoTouched(self, repo):
        if repo in self._repoCommitCalcCache:
            del self._repoCommitCalcCache[repo]

    def _updateBranchTopCommit(self, branch):
        repo = self.source_control.getRepo(branch.repo.name)
        commit = repo.branchTopCommit(branch.branchname)

        logging.info('Top commit of %s branch %s is %s', repo, branch.branchname, commit)

        if commit and (not branch.head or commit != branch.head.hash):
            old_branch_head = branch.head

            self._setBranchHead(branch, self._lookupCommitByHash(branch.repo, commit))

            if old_branch_head:
                self._triggerCommitPriorityUpdate(old_branch_head)
            
            if branch.head:
                self._triggerCommitPriorityUpdate(branch.head)
                self._recalculateBranchPins(branch)
                
                for pin in self.database.BranchPin.lookupAll(pinned_to=(branch.repo.name,branch.branchname)):
                    self._scheduleBranchPinNeedsUpdating(pin.branch)

        if not branch.head.data:
            self._updateCommitData(branch.head)

        needingAnyBranchSet = set()
        if branch.head:
            needingAnyBranchSet.add(branch.head)

        while needingAnyBranchSet:
            commit = needingAnyBranchSet.pop()
            if not commit.anyBranch:
                commit.anyBranch = branch
                self._triggerCommitPriorityUpdate(commit)
                if commit.data:
                    for p in commit.data.parents:
                        needingAnyBranchSet.add(p)


    def _computeCommitPriority(self, commit):
        if commit.anyBranch:
            return commit.userEnabledTestSets
        else:
            return ()

    def _updateCommitPriority(self, commit):
        branch = self._calcCommitAnybranch(commit)
        changed = False
        if branch != commit.anyBranch:
            logging.info("Commit %s/%s changed anybranch from %s to %s", 
                commit.repo.name,
                commit.hash,
                commit.anyBranch.branchname if commit.anyBranch else "<none>",
                branch.branchname if branch else "<none>"
                )
            commit.anyBranch = branch
            changed = True

        testSets = self._computeCommitPriority(commit)
        
        if testSets != commit.calculatedTestSets:
            logging.info("Commit %s/%s changed testSets from %s to %s.", 
                commit.repo.name,
                commit.hash,
                commit.calculatedTestSets,
                testSets
                )

            commit.calculatedTestSets = testSets
            changed = True
        else:
            logging.info("Commit %s/%s has testSets %s and anybranch=%s.", 
                commit.repo.name,
                commit.hash,
                testSets,
                commit.anyBranch.branchname if commit.anyBranch else "<none>"
                )

        if commit.data and changed:
            for p in commit.data.parents:
                self._triggerCommitPriorityUpdate(p)

            #trigger testSets updates of all builds in other commits
            for test in self.allTestsForCommit(commit):
                logging.info("Because commit priority chaanged, triggering update of %s (%s)", test.hash, test.testDefinitionSummary.name)
                self._triggerTestPriorityUpdate(test)

    def _calcCommitAnybranch(self, commit):
        #calculate any branch that this commit can reach
        branches = set()

        for branch in self.database.Branch.lookupAll(head=commit):
            branches.add(branch)

        #look at all our parents and see where they come from
        for r in self.database.CommitRelationship.lookupAll(parent=commit):
            if r.child.anyBranch:
                branches.add(r.child.anyBranch)

        if not branches:
            return self.database.Branch.Null

        #no reason to change it if we don't need to
        if commit.anyBranch and commit.anyBranch in branches:
            return commit.anyBranch

        return sorted(branches, key=lambda b: b.branchname)[0]

    def _scheduleUpdateBranchTopCommit(self, branch):
        self._queueTask(
            self.database.DataTask.New(
                task=self.database.BackgroundTask.UpdateBranchTopCommit(branch),
                status=pendingVeryHigh
                )
            )

    def _recalculateBranchPins(self, branch):
        existingPins = self.database.BranchPin.lookupAll(branch=branch)

        for p in existingPins:
            p.delete()

        if branch.head and branch.head.data:
            for repo_def, target in branch.head.data.repos.iteritems():
                reponame = target.reponame()

                if target.matches.Pin:
                    self.database.BranchPin.New(
                        branch=branch, 
                        repo_def=repo_def, 
                        pinned_to_repo=reponame,
                        pinned_to_branch=target.branch,
                        auto=target.auto
                        )

        self._scheduleBranchPinNeedsUpdating(branch)

    def _scheduleBranchPinNeedsUpdating(self, branch):
        self._queueTask(
            self.database.DataTask.New(
                task=self.database.BackgroundTask.UpdateBranchPins(branch=branch),
                status=pendingLow
                )
            )

    def _setupContentsForMachineCategory(self, category):
        for test in self.database.Test.lookupAll(machineCategoryAndPrioritized=category):
            try:            
                env = self.environmentForTest(test)
                return env.image.setup_script_contents
            except:
                logging.error("Couldn't get an environment for test %s" % test.hash)

    def _bootMachinesIfNecessary(self, curTimestamp, curLock):
        #repeatedly check if we can boot any machines. If we can't,
        #but we want to, we need to check whether there are any machines we can
        #shut down
        logging.info("Entering _bootMachinesIfNecessary with %s cores currently booted across %s machines.", 
            self.machine_management.cores_booted, 
            len(self.machine_management.runningMachines)
            )
        for cat in (self.database.MachineCategory.lookupAll(want_more=True) +  
                            self.database.MachineCategory.lookupAll(want_less=True)):
            logging.info("\t%s/%s: %s desired vs %s booted. Bootable=%s", cat.hardware, cat.os, cat.desired, cat.booted, not cat.hardwareComboUnbootable)

        if curTimestamp - self.lastAmiCheckTimestamp > AMI_CHECK_INTERVAL:
            logging.info("Checking whether we need to build any AMIs")
            self.lastAmiCheckTimestamp = curTimestamp
            self.machine_management.amiCollectionCheck()

        def check():
            wantingBoot = self.database.MachineCategory.lookupAll(want_more=True)
            wantingShutdown = self.database.MachineCategory.lookupAll(want_less=True)

            def canBoot():
                for c in wantingBoot:
                    if self.machine_management.canBoot(c.hardware, c.os):
                        return c
                    elif self.machine_management.wantsToSeeSetupScriptForOsConfig(c.os):
                        setupContents = self._setupContentsForMachineCategory(c)
                        if setupContents:
                            self.machine_management.ensureOsConfigAvailable(c.os, setupContents)
                        else:
                            logging.warn("Can't find setup contents for %s/%s", c.os, c.hardware)
                    elif self.machine_management.isOsConfigInvalid(c.os):
                        c.hardwareComboUnbootable=True
                        c.hardwareComboUnbootableReason="Ami creation failed"
                        c.desired=0



            while wantingBoot and not canBoot() and wantingShutdown:
                shutAnyDown = False

                for possibleCategory in wantingShutdown:
                    if not shutAnyDown and self._shutdown(possibleCategory, curTimestamp, onlyIdle=False):
                        shutAnyDown = True

                if not shutAnyDown:
                    break

            c = canBoot()

            if c:
                return self._boot(c, curTimestamp, curLock)
            else:
                return False

        while check():
            pass


    def _boot(self, category, curTimestamp, curLock):
        """Try to boot a machine from 'category'. Returns True if booted."""
        try:
            logging.info("Trying to boot %s/%s to meet requirements of %s desired (%s booted now)", 
                category.hardware, 
                category.os, 
                category.desired, 
                category.booted
                )
            try:
                if curLock:
                    curLock.__exit__(None, None, None)
                machineId = self.machine_management.boot_worker(category.hardware, category.os)
            finally:
                if curLock:
                    curLock.__enter__()
        except MachineManagement.UnbootableWorkerCombination as e:
            category.hardwareComboUnbootable=True
            category.hardwareComboUnbootableReason="Invalid hardware/os combination"
            category.desired=0

            for t in self.database.Test.lookupAll(machineCategoryAndPrioritized=category):
                self._triggerTestPriorityUpdate(t)

            return False
        except:
            logging.error("Failed to boot a worker (%s,%s):\n%s", category.hardware, category.os, traceback.format_exc())
            return False

        self.database.Machine.New(
            machineId=machineId,
            hardware=category.hardware,
            bootTime=curTimestamp,
            os=category.os,
            isAlive=True
            )
        category.booted = category.booted + 1
        return True

    def _anythingRunningOnMachine(self, machine):
        return self.database.TestRun.lookupAny(runningOnMachine=machine) or self.database.Deployment.lookupAny(runningOnMachine=machine)

    def _machineLooksIdle(self, machine, curTimestamp):
        if machine.os.matches.WindowsVM:
            #we get billed in hourly increments on windows machines
            #so we don't shut them down immediately
            if curTimestamp - machine.bootTime < 60 * 55:
                return False

        if curTimestamp - machine.lastTestCompleted > IDLE_TIME_BEFORE_SHUTDOWN:
            logging.info("Machine %s looks idle for %s seconds", machine.machineId, curTimestamp - machine.lastTestCompleted)
            return True
        return False

    def _shutdown(self, category, curTimestamp, onlyIdle):
        for machine in self.database.Machine.lookupAll(hardware_and_os=(category.hardware, category.os)):
            assert machine.isAlive

            if not self._anythingRunningOnMachine(machine):
                if not onlyIdle or self._machineLooksIdle(machine, curTimestamp):
                    if not DISABLE_MACHINE_TERMINATION:
                        self._terminateMachine(machine, curTimestamp)
                    return True
        return False

    def _shutdownMachinesIfNecessary(self, curTimestamp):
        if DISABLE_MACHINE_TERMINATION:
            return

        def check():
            shutDownAny = False

            for cat in self.database.MachineCategory.lookupAll(want_less=True):
                if cat.desired < cat.booted:
                    if self._shutdown(cat, curTimestamp, onlyIdle=True):
                        shutDownAny = True

            return shutDownAny

        while check():
            pass

    def _terminateMachine(self, machine, curTimestamp):
        logging.info("Actively terminating machine %s", machine.machineId)
        try:
            self.machine_management.terminate_worker(machine.machineId)
        except:
            logging.error("Failed to terminate worker %s because:\n%s\n\nCalled from:\n%s", 
                machine.machineId, 
                traceback.format_exc(),
                "".join(traceback.format_stack())
                )

        self._machineTerminated(machine.machineId, curTimestamp)

    def _setCommitUserEnabledTestSets(self, commit, testSets):
        if testSets != commit.userEnabledTestSets:
            logging.info("Commit %s/%s has new enabled test sets %s (old=%s)", 
                commit.repo.name, 
                commit.hash, 
                testSets,
                commit.userEnabledTestSets
                )

            commit.userEnabledTestSets = testSets
            self._triggerCommitPriorityUpdate(commit)

    def oldestCommitForTest(self, test):
        commits = self.commitsReferencingTest(test)
        if not commits:
            return None
        return sorted(commits, key=lambda c: c.data.timestamp)[0]

    def commitsReferencingTest(self, test):
        return [dep.commit for dep in self.database.CommitTestDependency.lookupAll(test=test)]

    def _updateTestPriority(self, test, curTimestamp):
        oldCalcPri = test.calculatedPriority

        test.calculatedPriority = 0

        #compute the direct prioritization
        for commit in self.commitsReferencingTest(test):
            if commit.data:
                for testSet in commit.calculatedTestSets:
                    tests = commit.data.testSetsTopLevel.get(testSet, [])
                    if test.testDefinitionSummary.name in tests:
                        test.calculatedPriority = 1

        #now check all tests that depend on us and see if any of them is prioritized
        for dep in self.database.TestDependency.lookupAll(dependsOn=test):
            if dep.test.calculatedPriority > 0:
                test.calculatedPriority = 1

        #cancel any runs already going if this gets deprioritized
        if test.calculatedPriority == 0:
            for run in self.database.TestRun.lookupAll(test=test):
                if run.endTimestamp == 0.0 and not run.canceled:
                    logging.info("Canceling testRun %s because its commit priority went to zero.", run._identity)
                    self._cancelTestRun(run, curTimestamp)

        oldPriority = test.priority
        oldTargetMachineBoot = test.targetMachineBoot

        category = test.machineCategory = self._machineCategoryForTest(test)

        if category and category.hardwareComboUnbootable:
            logging.warn("Can't boot test %s because the hardware combo is unbootable.", test.hash)
            test.priority = self.database.TestPriority.HardwareComboUnbootable()
            test.targetMachineBoot = 0
            test.calculatedPriority = 0
        elif self._testHasUnresolvedDependencies(test):
            test.priority = self.database.TestPriority.UnresolvedDependencies()
            test.targetMachineBoot = 0
        elif self._testHasFailedDeps(test):
            test.priority = self.database.TestPriority.DependencyFailed()
            test.targetMachineBoot = 0
        elif self._testHasUnfinishedDeps(test):
            test.priority = self.database.TestPriority.WaitingOnBuilds()
            test.targetMachineBoot = 0
        else:
            #sets test.targetMachineBoot
            if self._updateTestTargetMachineCountAndReturnIsDone(test, curTimestamp):
                if self._testWantsRetries(test):
                    if test.activeRuns:
                        #if we have an active test, then it is itself a retry and we don't
                        #need more
                        test.priority = self.database.TestPriority.NoMoreTests()
                    else:
                        #but if we have no runs, then we're in a wait-state
                        test.priority = self.database.TestPriority.WaitingToRetry()
                else:
                    test.priority = self.database.TestPriority.NoMoreTests()
            elif test.testDefinitionSummary.type == "Build":
                test.priority = self.database.TestPriority.FirstBuild(priority=test.calculatedPriority)
            elif (test.totalRuns + test.activeRuns) == 0:
                test.priority = self.database.TestPriority.FirstTest(priority=test.calculatedPriority)
            else:
                test.priority = self.database.TestPriority.WantsMoreTests(priority=test.calculatedPriority)

        if category:
            net_change = test.targetMachineBoot - oldTargetMachineBoot

            if net_change != 0:
                category.desired = category.desired + net_change
                self._scheduleBootCheck()

        
        if test.priority != oldPriority or test.calculatedPriority != oldCalcPri:
            for dep in self.database.TestDependency.lookupAll(test=test):
                self._updateTestPriority(dep.dependsOn, curTimestamp)
                #self._triggerTestPriorityUpdate(dep.dependsOn)
            for dep in self.database.TestDependency.lookupAll(dependsOn=test):
                self._updateTestPriority(dep.test, curTimestamp)
                #self._triggerTestPriorityUpdate(dep.test)

        logging.info(
            "test priority for test %s is now %s. targetBoot=%s", 
            test.hash, 
            test.priority, 
            test.targetMachineBoot
            )
      


    def _checkMachineCategoryCounts(self):
        desired = {}
        booted = {}

        for machine in self.database.Machine.lookupAll(isAlive=True):
            cat = self._machineCategoryForPair(machine.hardware, machine.os)
            if cat:
                if cat not in desired:
                    desired[cat] = 0
                if cat not in booted:
                    booted[cat] = 0
                booted[cat] += 1

        for cat in self.database.MachineCategory.lookupAll(want_less=True):
            assert cat
            if cat not in desired:
                desired[cat] = 0

        for cat in self.database.MachineCategory.lookupAll(want_more=True):
            assert cat
            if cat not in desired:
                desired[cat] = 0

        def checkTestCategory(test):
            real_cat = self._machineCategoryForTest(test)
            if not real_cat:
                logging.warn("Test %s/%s has no machine category!", test.hash, test.name)
                self._checkAllTestDependencies(test)
                real_cat = self._machineCategoryForTest(test)
                if not real_cat:
                    logging.warn("Test %s/%s STILL has no machine category!", test.hash, test.name)


            if real_cat != test.machineCategory:
                logging.warn("test %s had incorrect desired machine category %s != %s", 
                    test.hash + "/" + test.testDefinitionSummary.name, 
                    "<none>" if not test.machineCategory else
                        str(test.machineCategory.hardware) + "/" + str(test.machineCategory.os),
                    "<none>" if not real_cat else
                        str(real_cat.hardware) + "/" + str(real_cat.os)
                    )

                if real_cat is None:
                    real_cat = self.database.MachineCategory.Null

                test.machineCategory = real_cat            

        for testRun in self.database.TestRun.lookupAll(isRunning=True):
            cat = testRun.test.machineCategory
            if cat:
                if cat not in desired:
                    desired[cat] = 0
                desired[cat] += 1

        for priorityType in [
                self.database.TestPriority.FirstBuild,
                self.database.TestPriority.FirstTest,
                self.database.TestPriority.WantsMoreTests
                ]:
            for priority in reversed(range(1,MAX_TEST_PRIORITY+1)):
                for test in self.database.Test.lookupAll(priority=priorityType(priority)):
                    checkTestCategory(test)

                    cat = test.machineCategory
                    if cat:
                        if cat not in desired:
                            desired[cat] = 0
                        desired[cat] += 1

        for deployment in self.database.Deployment.lookupAll(isAlive=True):
            checkTestCategory(deployment.test)

            cat = deployment.test.machineCategory
            if cat:
                if cat not in desired:
                    desired[cat] = 0

                desired[cat] += 1

        for cat, desiredCount in desired.iteritems():
            if cat not in booted:
                booted[cat] = 0

        for cat in booted:
            bootedCount = booted[cat]
            desiredCount = desired[cat]

            if cat.desired != desiredCount:
                logging.error("Category %s/%s had incorrect desire count: %s actual vs %s in db. adjusting", cat.hardware, cat.os, desiredCount, cat.desired)
                cat.desired = desiredCount

            if cat.booted != bootedCount:
                logging.error("Category %s/%s had incorrect boot count: %s actual vs %s in db. adjusting", cat.hardware, cat.os, bootedCount, cat.booted)
                cat.booted = bootedCount

        for cat in booted:
            logging.error("Category %s=%s/%s final state: %s desired, %s booted", cat._identity[:6], cat.hardware, cat.os, cat.desired, cat.booted)
                

    def _machineCategoryForTest(self, test):
        hardware_and_os = self._machineCategoryPairForTest(test)
        
        if hardware_and_os is None:
            return None
        
        return self._machineCategoryForPair(hardware_and_os[0], hardware_and_os[1])

    def _machineCategoryForPair(self, hardware, os):
        hardware_and_os = (hardware, os)

        cat = self.database.MachineCategory.lookupAny(hardware_and_os=hardware_and_os)
        if cat:
            return cat

        return self.database.MachineCategory.New(
            hardware=hardware_and_os[0],
            os=hardware_and_os[1]
            )

    def _machineCategoryPairForTest(self, test):
        os = test.testDefinitionSummary.machineOs

        min_cores = test.testDefinitionSummary.min_cores
        max_cores = test.testDefinitionSummary.max_cores
        min_ram_gb = test.testDefinitionSummary.min_ram_gb

        viable = []

        for hardware in self.machine_management.all_hardware_configs():
            if hardware.cores >= min_cores and hardware.ram_gb >= min_ram_gb:
                viable.append(hardware)

        if not viable:
            logging.warn("Test %s has no viable hardware configurations", test.hash + "/" + test.name)
            return None

        if max_cores:
            #we want the biggest machine that's less than this
            smaller = [x for x in viable if x.cores <= max_cores]

            #if none are available, still do something!
            if not smaller:
                return (viable[0], os)

            #otherwise, take the largest one we can
            return (smaller[-1], os)
        else:
            viable = sorted(viable, key=lambda k: k.cores)

            logging.info("Viable machine types for (%s/%s/%s) are %s. Taking the smallest.", 
                min_cores, max_cores, min_ram_gb, viable
                )

            return (viable[0], os)

    def _updateTestTargetMachineCountAndReturnIsDone(self, test, curTimestamp):
        if test.calculatedPriority == 0:
            test.targetMachineBoot = 0
            return True

        if test.testDefinitionSummary.type == "Deployment":
            needed = 0
        elif test.testDefinitionSummary.type == "Build":
            if self._testWantsRetries(test):
                if self._readyToRetryTest(test, curTimestamp):
                    needed = test.totalRuns + 1
                else:
                    needed = test.totalRuns
            else:
                needed = 1
        else:
            needed = max(test.runsDesired, 1)

        test.targetMachineBoot = max(needed - test.totalRuns, 0)

        return test.totalRuns + test.activeRuns >= needed

    def _testHasUnresolvedDependencies(self, test):
        return self.database.UnresolvedTestDependency.lookupAll(test=test)

    def _testHasUnfinishedDeps(self, test):
        deps = self.database.TestDependency.lookupAll(test=test)

        for dep in deps:
            if not self._testHasArtifactAnywhere(dep.dependsOn, dep.artifact):
                if dep.dependsOn.totalRuns == 0:
                    return True
                if self._testWantsRetries(dep.dependsOn):
                    return True

        return False

    def _testHasArtifactAnywhere(self, test, artifact):
        for run in self.database.TestRun.lookupAll(test=test):
            if not run.canceled and artifact in run.artifactsCompleted:
                return True
        return False


    def _testHasFailedDeps(self, test):
        for dep in self.database.TestDependency.lookupAll(test=test):
            if not self._testHasArtifactAnywhere(dep.dependsOn, dep.artifact):
                if dep.dependsOn.totalRuns > 0 and dep.dependsOn.successes == 0 and not self._testWantsRetries(dep.dependsOn) or \
                        dep.dependsOn.priority.matches.DependencyFailed:
                    return True
        return False

    def _readyToRetryTest(self, test, curTimestamp):
        if test.totalRuns == 0:
            return True

        return curTimestamp - test.lastTestEndTimestamp > test.testDefinitionSummary.retry_wait_seconds

    def _testWantsRetries(self, test):
        if test.calculatedPriority == 0:
            return False
        
        if not test.testDefinitionSummary.type == "Build":
            return False

        if test.testDefinitionSummary.max_retries == 0:
            return False

        if test.successes:
            return False

        if test.totalRuns < test.testDefinitionSummary.max_retries:
            return True

        return False

    def _setTestParentIfPossible(self, test):
        if test.parentChecked:
            return

        commits = self.commitsReferencingTest(test)
        if not commits:
            return

        for c in commits:
            if not c.data:
                return

        #pick the oldest commit
        commit = sorted(commits, key=lambda c: c.data.timestamp)[0]


        testName = test.testDefinitionSummary.name
        while True:
            if not commit.data:
                #we're not done parsing tests
                return

            #a test by the same name in a parent commit but with different content is
            #our actual parent commit
            if testName in commit.data.tests and commit.data.tests.get(testName) != test:
                test.parent = commit.data.tests.get(testName)
                test.parentChecked = True
                return

            #if it's not in the parent commit, then we bail early. no reaon to check back
            #over some enormous history.
            if testName not in commit.data.tests:
                #this set of tests isn't present
                test.parentChecked = True
                return

            #if the commit history ends, we also bail
            if not commit.data.parents:
                test.parentChecked=True
                return

            commit = commit.data.parents[0]
    
    def _createTest(self, commit, testDefinition):
        #make sure it's new
        test = self.database.Test.lookupAny(hash=testDefinition.hash)
        
        if not test:
            test = self.database.Test.New(
                hash=testDefinition.hash,
                testResultSummary=self.database.TestResultSummary.New(),
                testDefinitionSummary=self.database.TestDefinitionSummary.Summary(
                    machineOs=self._machineOsForEnv(testDefinition.environment),
                    artifacts=[a.name for stage in testDefinition.stages for a in stage.artifacts],
                    name=testDefinition.name,
                    type=testDefinition._which,
                    configuration=testDefinition.configuration,
                    project=testDefinition.project,
                    timeout=testDefinition.timeout,
                    min_cores=testDefinition.min_cores,
                    max_cores=testDefinition.max_cores,
                    min_ram_gb=testDefinition.min_ram_gb,
                    min_disk_gb=testDefinition.min_disk_gb,
                    max_retries=testDefinition.max_retries if testDefinition.matches.Build else 0,
                    retry_wait_seconds=testDefinition.retry_wait_seconds if testDefinition.matches.Build else 0
                    ),
                priority=self.database.TestPriority.NoMoreTests()
                )
            test.testResultSummary.test = test

            self._checkAllTestDependencies(test, testDefinition)
            self._markTestCreated(test)
            self._triggerTestPriorityUpdate(test)

        self.database.CommitTestDependency.New(commit=commit,test=test)

        return test

    def _machineOsForEnv(self, env):
        if env.platform.matches.linux:
            if env.image.matches.Dockerfile or env.image.matches.DockerfileInline:
                return MachineManagement.OsConfig.LinuxWithDocker()
            elif env.image.matches.AMI:
                return MachineManagement.OsConfig.LinuxVM(
                    ami=env.image.base_ami,
                    setupHash=sha_hash(env.image.setup_script_contents).hexdigest
                    )
            else:
                logging.warn("Test %s has an invalid image %s for linux", test.hash + "/" + test.name, env.image)
                return None

        if env.platform.matches.windows:
            if env.image.matches.Dockerfile or env.image.matches.DockerfileInline:
                return MachineManagement.OsConfig.WindowsWithDocker()
            elif env.image.matches.AMI:
                return MachineManagement.OsConfig.WindowsVM(
                    ami=env.image.base_ami,
                    setupHash=sha_hash(env.image.setup_script_contents).hexdigest
                    )
            else:
                logging.warn("Test %s has an invalid image %s for windows", test.hash + "/" + test.name, env.image)
                return None


    def _checkAllTestDependencies(self, test, testDefinition):
        env = testDefinition.environment

        #here we should have a fully populated environment, with all dependencies
        #resolved
        assert env.matches.Environment

        test.machineCategory = self._machineCategoryForTest(test)
                
        all_dependencies = dict(env.dependencies)
        all_dependencies.update(testDefinition.dependencies)

        #now first check whether this test has any unresolved dependencies
        for depname, dep in all_dependencies.iteritems():
            if dep.matches.ExternalBuild:
                assert False
            elif dep.matches.InternalBuild:
                assert False
            elif dep.matches.Build:
                self._createTestDep(test, dep.buildHash, dep.artifact)
            elif dep.matches.Source:
                pass

    def _createSourceDep(self, commit, reponame, commitHash):
        repo = self.database.Repo.lookupAny(name=reponame)
        if not repo:
            if self.database.UnresolvedCommitRepoDependency.lookupAny(commit_and_reponame=(commit, reponame)) is None:
                self.database.UnresolvedCommitRepoDependency.New(commit=commit,reponame=reponame)
            return True

        if commitHash:
            foundCommit = self.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))
            if not foundCommit or not foundCommit.data:
                if self.database.UnresolvedCommitSourceDependency.lookupAny(commit_and_repo_and_hash=(commit, repo, commitHash)) is None:
                    self.database.UnresolvedCommitSourceDependency.New(commit=commit, repo=repo, commitHash=commitHash)
                return True

        return False

    def _createTestDep(self, test, childHash, artifact):
        dep_test = self.database.Test.lookupAny(hash=childHash)

        assert dep_test != test

        if not dep_test:
            if self.database.UnresolvedTestDependency.lookupAny(test_and_depends=(test, childHash,artifact)) is None:
                self.database.UnresolvedTestDependency.New(test=test, dependsOnHash=childHash,artifact=artifact)
        else:
            if self.database.TestDependency.lookupAny(test_and_depends=(test, dep_test, artifact)) is None:
                self.database.TestDependency.New(test=test, dependsOn=dep_test,artifact=artifact)

    def _markTestCreated(self, test):
        for dep in self.database.UnresolvedTestDependency.lookupAll(dependsOnHash=test.hash):
            self.database.TestDependency.New(test=dep.test, dependsOn=test,artifact=dep.artifact)
            self._triggerTestPriorityUpdate(dep.test)
            dep.delete()
                
    def _triggerCommitPriorityUpdate(self, commit):
        if not self.database.DataTask.lookupAny(update_commit_priority=commit):
            self._queueTask(
                self.database.DataTask.New(
                    task=self.database.BackgroundTask.UpdateCommitPriority(commit=commit),
                    status=pendingMedium
                    )
                )

    def _triggerTestPriorityUpdate(self, test):
        #test priority updates are always 'low' because we want to ensure
        #that all commit updates have triggered first. This way we know that
        #we're not accidentally going to cancel a test
        if not self.database.DataTask.lookupAny(update_test_priority=test):
            self._queueTask(
                self.database.DataTask.New(
                    task=self.database.BackgroundTask.UpdateTestPriority(test=test),
                    status=pendingLow
                    )
                )

    def _triggerCommitTestParse(self, commit):
        if not (self.database.UnresolvedCommitRepoDependency.lookupAll(commit=commit) +
                self.database.UnresolvedCommitSourceDependency.lookupAll(commit=commit)
                ):
            self._queueTask(
                self.database.DataTask.New(
                    task=self.database.BackgroundTask.CommitTestParse(commit=commit),
                    status=pendingHigh
                    )
                )

    def _createRepo(self, new_repo_name):
        r = self.database.Repo.New(name=new_repo_name,isActive=True)

        for dep in self.database.UnresolvedCommitRepoDependency.lookupAll(reponame=new_repo_name):
            self._createSourceDep(dep.commit, new_repo_name, None)
            commit = dep.commit

            #delete this first, since we check to see if any such dependencies exist!
            dep.delete()

            self._triggerCommitTestParse(commit)

        return r

    def _lookupCommitByHash(self, repo, commitHash, create=True):
        if isinstance(repo, str):
            repoName = repo
            repo = self.database.Repo.lookupAny(name=repo)
            if not repo:
                logging.warn("Unknown repo %s while looking up %s/%s", repo, repoName, commitHash)
                return None

        commit = self.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))

        if not commit:
            if not create:
                return None
            
            commit = self.database.Commit.New(repo=repo, hash=commitHash)
            repo.commits = repo.commits + 1
            self._repoTouched(repo)

        if not commit.data:
            self._triggerCommitDataUpdate(commit)

        return commit

    def _triggerCommitDataUpdate(self, commit):
        if not commit.data:
            self._queueTask(
                self.database.DataTask.New(
                    task=self.database.BackgroundTask.UpdateCommitData(commit=commit),
                    status=pendingHigh
                    )
                )


    def _queueTask(self, task):
        existing = self.database.DataTask.lookupAny(status=task.status)

        task.isHead = True

        if existing:
            existing.isHead = False
            task.prior = existing
            task.prior_ct = existing.prior_ct + 1
