import collections
import logging
import random
import time
import traceback
import simplejson
import threading
import textwrap
import re
from test_looper.core.hash import sha_hash
import test_looper.core.Bitstring as Bitstring
import test_looper.core.object_database as object_database
import test_looper.core.algebraic as algebraic
import test_looper.core.machine_management.MachineManagement as MachineManagement
import test_looper.data_model.Types as Types
import test_looper.data_model.BranchPinning as BranchPinning
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.data_model.TestDefinitionResolver as TestDefinitionResolver

pendingVeryHigh = Types.BackgroundTaskStatus.PendingVeryHigh()
pendingHigh = Types.BackgroundTaskStatus.PendingHigh()
pendingMedium = Types.BackgroundTaskStatus.PendingMedium()
pendingLow = Types.BackgroundTaskStatus.PendingLow()
running = Types.BackgroundTaskStatus.Running()

MAX_TEST_PRIORITY = 2
TEST_TIMEOUT_SECONDS = 60
IDLE_TIME_BEFORE_SHUTDOWN = 180
MAX_LOG_MESSAGES_PER_TEST = 100000
MACHINE_TIMEOUT_SECONDS = 600
DISABLE_MACHINE_TERMINATION = False

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
        self.initialTimestamp = initialTimestamp or time.time()

        self.server_port_config = server_port_config
        self.source_control = source_control
        self.machine_management = machine_management

        self.database = object_database.Database(kv_store)
        Types.setup_types(self.database)

        self.writelock = threading.RLock()

        self.heartbeatHandler = HeartbeatHandler()

        self.deploymentStreams = {}

    def allTestsDependedOnByTest(self, test):
        res = []

        for dep in test.testDefinition.dependencies.values():
            if dep.matches.InternalBuild:
                for subtest in self.database.Test.lookupAll(
                        fullname=test.commitData.commit.repo.name + "/" + test.commitData.commit.hash + "/" + dep.name
                        ):
                    res.append(subtest)
            elif dep.matches.ExternalBuild:
                for subtest in self.database.Test.lookupAll(
                        fullname=dep.repo + "/" + dep.commitHash + "/" + dep.name
                        ):
                    res.append(subtest)

        return res

    def bestCommitName(self, commit):
        branch, name = self.bestCommitBranchAndName(commit)
        if not branch:
            return name
        return branch.branchname + name

    def bestCommitBranchAndName(self, commit):
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

    def upstreamCommits(self, commit):
        if not commit.data:
            return []
        result = []

        for test in self.database.Test.lookupAll(commitData=commit.data):
            for dep in self.database.TestDependency.lookupAll(test=test):
                if commit != dep.dependsOn.commitData.commit:
                    result.append(dep.dependsOn.commitData.commit)

        return sorted(set(result), key=lambda k: k.repo.name + "/" + k.hash)

    def downstreamCommits(self, commit):
        if not commit.data:
            return []
        result = []

        for test in self.database.Test.lookupAll(commitData=commit.data):
            for dep in self.database.TestDependency.lookupAll(dependsOn=test):
                if commit != dep.test.commitData.commit:
                    result.append(dep.test.commitData.commit)

        return sorted(set(result), key=lambda k: k.repo.name + "/" + k.hash)

    def getNCommits(self, commit, N, direction="below"):
        """Do a breadth-first search around 'commit'"""

        assert direction in ("above", "below")

        commits = []
        seen = set()
        frontier = [commit]

        while frontier and len(commits) < N:
            c = frontier.pop(0)
            if c not in seen:
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

        deployment.machine.lastTestCompleted = timestamp

        logging.info("Setting last test completed on %s ", deployment.machine.machineId, timestamp)

        os = deployment.machine.os
        
        if (os.matches.WindowsVM or os.matches.LinuxVM):
            #we need to shut down this machine since it has a setup script
            if not DISABLE_MACHINE_TERMINATION:
                self._terminateMachine(deployment.machine, timestamp)

        self._scheduleBootCheck()
        self._shutdownMachinesIfNecessary(timestamp)

    def createDeployment(self, fullname, timestamp):
        with self.transaction_and_lock():
            logging.info("Trying to boot a deployment for %s", fullname)

            test = self.database.Test.lookupAny(fullname=fullname)

            if not test:
                raise Exception("Can't find test %s" % fullname)

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
                time.asctime() + " TestLooper> Deployment for %s waiting for hardware.\n\r" % fullname
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
        for test in self.database.Test.lookupAll(commitData=commit.data):
            res += max(0, test.activeRuns)

        return res

    def totalRunningCountForTest(self, test):
        return test.activeRuns

    def prioritizeAllCommitsUnderBranch(self, branch, priority, depth):
        commits = {}
        def check(c):
            if not c or c in commits or len(commits) >= depth:
                return

            commits[c] = True

            self._setCommitUserPriority(c, priority)

            for r in self.database.CommitRelationship.lookupAll(child=c):
                check(r.parent)

        check(branch.head)
        
    def toggleBranchUnderTest(self, branch):
        branch.isUnderTest = not branch.isUnderTest
        if branch.head and branch.head.userPriority == 0 and branch.isUnderTest:
            self._setCommitUserPriority(branch.head, 1)
        
    def getTestRunById(self, testIdentity):
        testIdentity = str(testIdentity)

        t = self.database.TestRun(testIdentity)
        if t.exists():
            return t

    def priority_for_test(self, fullname):
        with self.database.view() as v:
            test = self.database.Test.lookupAny(fullname=fullname)
            if test:
                return test.priority
            else:
                return None

    def machineInitialized(self, machineId, curTimestamp):
        with self.transaction_and_lock():
            machine = self.database.Machine.lookupAny(machineId=machineId)
            if machine:
                self._machineHeartbeat(machine, curTimestamp)
            else:
                logging.warn("Initialization from unknown machine %s", machineId)


    def machineHeartbeat(self, machineId, curTimestamp, msg=None):
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

            testRun.test.totalTestCount = testRun.test.totalTestCount - testRun.totalTestCount
            testRun.test.totalFailedTestCount = testRun.test.totalFailedTestCount - testRun.totalFailedTestCount
            testRun.canceled = True

            self._triggerTestPriorityUpdate(testRun.test)

    def _importTestRun(self, test, identity, startedTimestamp, lastHeartbeat, endTimestamp, success, 
                      canceled, testNameList, testFailureBits, testCount, failedTestCount):
        
        testRun = self.database.TestRun.New(
            _identity=identity,
            test=test,
            startedTimestamp=startedTimestamp,
            lastHeartbeat=lastHeartbeat,
            endTimestamp=endTimestamp,
            canceled=canceled,
            success=success,
            testNames=self._testNameSet(testNameList),
            testFailures=Bitstring.Bitstring(testFailureBits),
            totalTestCount=testCount,
            totalFailedTestCount=failedTestCount
            )

        if success:
            test.successes += 1

        if not canceled:
            if endTimestamp > 0.0:
                test.totalRuns += 1
            else:
                test.activeRuns += 1

        test.totalTestCount += testCount
        test.totalFailedTestCount += failedTestCount

        self._triggerTestPriorityUpdate(testRun.test)

    @staticmethod
    def configurationForTest(test):
        if test.testDefinition.configuration:
            return test.testDefinition.configuration
        else:
            return test.testDefinition.environment_name

    def recordTestResults(self, success, testId, testSuccesses, curTimestamp):
        with self.transaction_and_lock():
            testRun = self.database.TestRun(str(testId))

            if not testRun.exists():
                return False

            if testRun.canceled:
                return False

            testRun.endTimestamp = curTimestamp
            
            testRun.test.activeRuns = testRun.test.activeRuns - 1
            testRun.test.totalRuns = testRun.test.totalRuns + 1
            testRun.test.lastTestEndTimestamp = curTimestamp

            names = sorted(testSuccesses.keys())
            testRun.testNames = self._testNameSet(names)
            testRun.testFailures = Bitstring.Bitstring.fromBools([testSuccesses[n][0] for n in names])
            testRun.testHasLogs = Bitstring.Bitstring.fromBools([testSuccesses[n][1] for n in names])
            testRun.totalTestCount = len(names)
            testRun.totalFailedTestCount = len([n for n in names if not testSuccesses[n][0]])

            testRun.test.totalTestCount = testRun.test.totalTestCount + testRun.totalTestCount
            testRun.test.totalFailedTestCount = testRun.test.totalFailedTestCount + testRun.totalFailedTestCount

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

    def handleTestConnectionReinitialized(self, testId, timestamp, allLogs):
        self.heartbeatHandler.testHeartbeatReinitialized(testId, timestamp, allLogs)

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

    def checkAllTestPriorities(self, curTimestamp):
        with self.transaction_and_lock():
            self._checkAllTestPriorities(curTimestamp)

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

    def _checkAllTestPriorities(self, curTimestamp):
        logging.info("Checking all test priorities to ensure they are correct")

        total = 0

        if self._checkActiveRunsLooksCorrupt():
            logging.warn("Active runs looks corrupt. Rebuilding.")
            commitsWithTests = self._allCommitsWithPossibilityOfTests()

            for c in commitsWithTests:
                for test in self.database.Test.lookupAll(commitData=c.data):
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
        """Allocates a new test and returns (repoName, commitHash, testName, deploymentId) or (None,None,None, None) if no work."""
        with self.transaction_and_lock():
            machine = self.database.Machine.lookupAny(machineId=machineId)

            if machine is None or not machine.isAlive:
                logging.warn("Can't assign work to a machine we don't know about: %s", machineId)
                return None, None, None, None, None

            self._machineHeartbeat(machine, timestamp)

            for deployment in self.database.Deployment.lookupAll(isAliveAndPending=True):
                if self._machineCategoryForTest(deployment.test) == self._machineCategoryForPair(machine.hardware, machine.os):
                    deployment.machine = machine
                    
                    self.streamForDeployment(deployment._identity).addMessageFromDeployment(
                        time.asctime(time.gmtime(timestamp)) + 
                            " TestLooper> Machine %s accepting deployment.\n\r" % machineId
                        )
                    
                    test = deployment.test

                    return (test.commitData.commit.repo.name, test.commitData.commit.hash, 
                            test.testDefinition.name, deployment._identity, test.testDefinition)

            return None, None, None, None, None

    def _cleanupGitRepoLocks(self):
        cleaned = 0

        for lock in self.database.AllocatedGitRepoLocks.lookupAll(alive=True):
            if lock.testOrDeployId is None:
                logging.info("Deleted an invalid GitRepoLock.")
                lock.delete()
                cleaned += 1
            else:                
                testRun = self.database.TestRun(lock.testOrDeployId)
                if testRun.exists() and (testRun.canceled or testRun.endTimestamp > 0.0):
                    logging.info("Deleted a GitRepoLock because test %s is dead.", lock.testOrDeployId)
                    lock.delete()
                    cleaned += 1
                else:
                    deployment = self.database.Deployment(lock.testOrDeployId)
                    if deployment.exists() and not deployment.isAlive:
                        logging.info("Deleted a GitRepoLock because deployment %s is dead", lock.testOrDeployId)
                        lock.delete()
                        cleaned += 1

        if cleaned:
            logging.info("Cleaned up %s git dead repo locks. %s remaining.", 
                cleaned, 
                len(self.database.AllocatedGitRepoLocks.lookupAll(alive=True))
                )

    def tryToAllocateGitRepoLock(self, requestId, testOrDeployId):
        with self.transaction_and_lock():
            self._cleanupGitRepoLocks()

            if self.database.AllocatedGitRepoLocks.lookupAny(requestUniqueId=requestId):
                #lock already exists
                logging.info(
                    "Reiterating to %s that it has a git repo lock. There are still %s locks.",
                    testOrDeployId,
                    len(self.database.AllocatedGitRepoLocks.lookupAll(alive=True))
                    )
                return True

            if self.database.TestRun(testOrDeployId).exists():
                commitHash = self.database.TestRun(testOrDeployId).test.commitData.commit.hash
            elif self.database.Deployment(testOrDeployId).exists():
                commitHash = self.database.Deployment(testOrDeployId).test.commitData.commit.hash
            else:
                return False

            if len(self.database.AllocatedGitRepoLocks.lookupAll(alive=True)) > MAX_GIT_CONNECTIONS:
                return False
            if len(self.database.AllocatedGitRepoLocks.lookupAll(testOrDeployId=testOrDeployId)) > 1:
                return False
            if len(self.database.AllocatedGitRepoLocks.lookupAll(commitHash=commitHash)) > 1:
                return False

            self.database.AllocatedGitRepoLocks.New(requestUniqueId=requestId,testOrDeployId=testOrDeployId, commitHash=commitHash)
            logging.info(
                "Allocating a git repo lock to test/deploy %s. There are now %s",
                testOrDeployId,
                len(self.database.AllocatedGitRepoLocks.lookupAll(alive=True))
                )
            return True

    def gitRepoLockReleased(self, requestId):
        with self.transaction_and_lock():
            lock = self.database.AllocatedGitRepoLocks.lookupAny(requestUniqueId=requestId)
            if lock:
                testOrDeployId = lock.testOrDeployId

                lock.delete()

                logging.info(
                    "Released a git repo lock to test/deploy %s. There are now %s",
                    testOrDeployId,
                    len(self.database.AllocatedGitRepoLocks.lookupAll(alive=True))
                    )

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
        """Allocates a new test and returns (repoName, commitHash, testName, testId, testDefinition) or (None,None,None,None,None) if no work."""
        with self.transaction_and_lock():
            machine = self.database.Machine.lookupAny(machineId=machineId)

            if not machine or not machine.isAlive:
                return None, None, None, None, None

            self._machineHeartbeat(machine, timestamp)

        with self.transaction_and_lock():
            t0 = time.time()
            test = self._lookupHighestPriorityTest(machine, timestamp)
            if time.time() - t0 > .25:
                logging.warn("Took %s to get priority", time.time() - t0)

            if not test:
                return None, None, None, None, None

            test.activeRuns = test.activeRuns + 1

            machine = self.database.Machine.lookupOne(machineId=machineId)

            runningTest = self.database.TestRun.New(
                test=test,
                startedTimestamp=timestamp,
                lastHeartbeat=timestamp,
                machine=machine
                )

            self._updateTestPriority(test, timestamp)

            return (test.commitData.commit.repo.name, test.commitData.commit.hash, test.testDefinition.name, runningTest._identity, test.testDefinition)

    def performCleanupTasks(self, curTimestamp):
        #check all tests to see if we've exceeded the timeout and the test is dead
        with self.transaction_and_lock():
            for t in self.database.TestRun.lookupAll(isRunning=True):
                if t.lastHeartbeat < curTimestamp - TEST_TIMEOUT_SECONDS and curTimestamp - self.initialTimestamp > TEST_TIMEOUT_SECONDS:
                    logging.info("Canceling testRun %s because it has not had a heartbeat for a long time.", t._identity)
                    self._cancelTestRun(t, curTimestamp)

            for m in self.database.Machine.lookupAll(isAlive=True):
                heartbeat = max(m.lastHeartbeat, m.bootTime)

                if heartbeat < curTimestamp - MACHINE_TIMEOUT_SECONDS and \
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
        with self.transaction_and_lock():
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
                    return

                testDef = task.task
                task.status = running

                try:
                    self._processTask(task.task, curTimestamp)
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
                return
                
            task.status = running

            testDef = task.task

        try:
            with self.transaction_and_lock():
                self._processTask(testDef, curTimestamp)
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

    def _processTask(self, task, curTimestamp):
        if task.matches.RefreshRepos:
            self._refreshRepos()
        elif task.matches.RefreshBranches:
            self._refreshBranches(task.repo)
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
            self._bootMachinesIfNecessary(curTimestamp)
        elif task.matches.UpdateCommitPriority:
            self._updateCommitPriority(task.commit)
        else:
            raise Exception("Unknown task: %s" % task)

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
                    status=pendingVeryHigh
                    )
                )

    def _refreshBranches(self, db_repo):
        repo = self.source_control.getRepo(db_repo.name)

        try:
            if not self.source_control.isWebhookInstalled(db_repo.name, self.server_port_config):
                self.source_control.installWebhook(
                    db_repo.name, 
                    self.server_port_config
                    )
        except:
            logging.error("Tried to install webhook for %s but failed: %s", 
                db_repo.name,
                traceback.format_exc()
                )

        repo.source_repo.fetchOrigin()

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
                self._branchDeleted(branch)
                branch.delete()

        for newname in branchnames_set - set([x.branchname for x in db_branches]):
            newbranch = self.database.Branch.New(branchname=newname, repo=db_repo)

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
        logging.info("Updating commit data for %s/%s", commit.repo.name, commit.hash)
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


    def _updateCommitDataForHash(self, repo, hash, timestamp, subject, body, author, authorEmail, parentHashes, knownNoTestFile=False):
        source_control_repo = self.source_control.getRepo(repo.name)

        commit = self._lookupCommitByHash(repo, hash)

        if commit is None or commit.data:
            logging.info("Not updating commit %s because it has commit.data and doesn't want a refresh.", commit.hash)
            return

        parents=[self._lookupCommitByHash(commit.repo, p) for p in parentHashes]

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
        priority = max([r.child.userPriority
            for r in self.database.CommitRelationship.lookupAll(parent=commit)] + [0])

        for branch in self.database.Branch.lookupAll(head=commit):
            if branch.isUnderTest:
                priority = max(priority, 1)

        commit.userPriority = max(commit.userPriority, priority)

        if knownNoTestFile:
            commit.data.noTestsFound = True
        #ignore commits produced before the looper existed. They won't have these files!
        elif commit.data.timestamp > OLDEST_TIMESTAMP_WITH_TESTS:
            logging.info("Loading data for commit %s with timestamp %s", commit.hash, time.asctime(time.gmtime(commit.data.timestamp)))
            self._triggerCommitPriorityUpdate(commit)

            self._parseCommitTests(commit)
        else:
            logging.info("Not loading data for commit %s with timestamp %s", commit.hash, time.asctime(time.gmtime(commit.data.timestamp)))

            commit.data.noTestsFound = True
            commit.data.testDefinitionsError = "Commit old enough that we won't check for test definitions."

        for branch in self.database.Branch.lookupAll(head=commit):
            self._recalculateBranchPins(branch)

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

            try:
                all_tests, all_environments, all_repo_defs = \
                    resolver.fullyResolvedTestEnvironmentAndRepoDefinitionsFor(
                        commit.repo.name, 
                        commit.hash
                        )

            except TestDefinitionResolver.MissingDependencyException as e:
                self._createSourceDep(commit, e.reponame, e.commitHash)
                return

            commit.data.testDefinitions = all_tests
            commit.data.environments = all_environments
            commit.data.repos = all_repo_defs
            
            for e in all_tests.values():
                fullname=commit.repo.name + "/" + commit.hash + "/" + e.name

                self._createTest(
                    commitData=commit.data,
                    fullname=fullname,
                    testDefinition=e
                    )

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

    def _branchDeleted(self, branch):
        old_branch_head = branch.head
        if old_branch_head:
            branch.head = self.database.Commit.Null
            self._triggerCommitPriorityUpdate(old_branch_head)

    def _updateBranchTopCommit(self, branch):
        repo = self.source_control.getRepo(branch.repo.name)
        commit = repo.branchTopCommit(branch.branchname)

        logging.info('Top commit of %s branch %s is %s', repo, branch.branchname, commit)

        if commit and (not branch.head or commit != branch.head.hash):
            old_branch_head = branch.head

            branch.head = self._lookupCommitByHash(branch.repo, commit)

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
            return commit.userPriority
        else:
            return 0

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

        priority = self._computeCommitPriority(commit)
        
        if priority != commit.calculatedPriority:
            logging.info("Commit %s/%s changed priority from %s to %s", 
                commit.repo.name,
                commit.hash,
                commit.calculatedPriority,
                priority
                )

            commit.calculatedPriority = priority
            changed = True

        if commit.data and changed:
            for p in commit.data.parents:
                self._triggerCommitPriorityUpdate(p)

            #trigger priority updates of all builds in other commits
            for test in self.database.Test.lookupAll(commitData=commit.data):
                for dep in self.database.TestDependency.lookupAll(test=test):
                    if commit != dep.dependsOn.commitData.commit:
                        self._triggerTestPriorityUpdate(dep.dependsOn)
            
            for test in self.database.Test.lookupAll(commitData=commit.data):
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
                reponame = "/".join(target.reference.split("/")[:-1])

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

    def _bootMachinesIfNecessary(self, curTimestamp):
        #repeatedly check if we can boot any machines. If we can't,
        #but we want to, we need to check whether there are any machines we can
        #shut down
        logging.info("Entering _bootMachinesIfNecessary:")
        for cat in (self.database.MachineCategory.lookupAll(want_more=True) +  
                            self.database.MachineCategory.lookupAll(want_less=True)):
            logging.info("\t%s/%s: %s desired vs %s booted", cat.hardware, cat.os, cat.desired, cat.booted)

        def check():
            wantingBoot = self.database.MachineCategory.lookupAll(want_more=True)
            wantingShutdown = self.database.MachineCategory.lookupAll(want_less=True)

            def canBoot():
                for c in wantingBoot:
                    if self.machine_management.canBoot(c.hardware, c.os):
                        return c

            while wantingBoot and not canBoot() and wantingShutdown:
                shutAnyDown = False

                for possibleCategory in wantingShutdown:
                    if not shutAnyDown and self._shutdown(possibleCategory, curTimestamp, onlyIdle=False):
                        shutAnyDown = True

                if not shutAnyDown:
                    break

            c = canBoot()

            if c:
                return self._boot(c, curTimestamp)
            else:
                return False

        while check():
            pass


    def _boot(self, category, curTimestamp):
        """Try to boot a machine from 'category'. Returns True if booted."""
        try:
            logging.info("Trying to boot %s/%s to meet requirements of %s desired (%s booted now)", 
                category.hardware, 
                category.os, 
                category.desired, 
                category.booted
                )

            machineId = self.machine_management.boot_worker(category.hardware, category.os)
        except MachineManagement.UnbootableWorkerCombination as e:
            category.hardwareComboUnbootable=True
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

    def _setCommitUserPriority(self, commit, priority):
        if priority != commit.userPriority:
            logging.info("Commit %s/%s has new priority %s (old=%s)", 
                commit.repo.name, 
                commit.hash, 
                priority,
                commit.userPriority
                )

            commit.userPriority = priority
            self._triggerCommitPriorityUpdate(commit)

    def _updateTestPriority(self, test, curTimestamp):
        self._checkAllTestDependencies(test)

        test.calculatedPriority = test.commitData.commit.calculatedPriority

        #now check all tests that depend on us
        for dep in self.database.TestDependency.lookupAll(dependsOn=test):
            test.calculatedPriority = max(dep.test.commitData.commit.calculatedPriority, test.calculatedPriority)

        #cancel any runs already going if this gets deprioritized
        if test.calculatedPriority == 0:
            for run in self.database.TestRun.lookupAll(test=test):
                if run.endTimestamp == 0.0 and not run.canceled:
                    logging.info("Canceling testRun %s because its commit priority went to zero.", run._identity)
                    self._cancelTestRun(run, curTimestamp)

        oldPriority = test.priority
        oldTargetMachineBoot = test.targetMachineBoot

        category = test.machineCategory

        if category and category.hardwareComboUnbootable:
            test.priority = self.database.TestPriority.HardwareComboUnbootable()
            test.targetMachineBoot = 0
        elif self._testHasUnresolvedDependencies(test):
            test.priority = self.database.TestPriority.UnresolvedDependencies()
            test.targetMachineBoot = 0
        elif self._testHasUnfinishedDeps(test):
            test.priority = self.database.TestPriority.WaitingOnBuilds()
            test.targetMachineBoot = 0
        elif self._testHasFailedDeps(test):
            test.priority = self.database.TestPriority.DependencyFailed()
            test.targetMachineBoot = 0
        else:
            #sets test.targetMachineBoot
            if not test.testDefinition.matches.Deployment and test.testDefinition.disabled:
                test.priority = self.database.TestPriority.NoMoreTests()
            elif self._updateTestTargetMachineCountAndReturnIsDone(test, curTimestamp):

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
            elif test.testDefinition.matches.Build:
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

        if test.priority != oldPriority:
            logging.info("test priority for test %s changed to %s", test.fullname, test.priority)
        
            for dep in self.database.TestDependency.lookupAll(test=test):
                self._triggerTestPriorityUpdate(dep.dependsOn)

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
                logging.warn("Test %s has no machine category!", test.fullname)
                self._checkAllTestDependencies(test)
                real_cat = self._machineCategoryForTest(test)
                if not real_cat:
                    logging.warn("Test %s STILL has no machine category!", test.fullname)


            if real_cat != test.machineCategory:
                logging.warn("test %s had incorrect desired machine category %s != %s", 
                    test.fullname, 
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
        env = test.testDefinition.environment

        if env.platform.matches.linux:
            if env.image.matches.Dockerfile or env.image.matches.DockerfileInline:
                os = MachineManagement.OsConfig.LinuxWithDocker()
            elif env.image.matches.AMI:
                os = MachineManagement.OsConfig.LinuxVM(env.image.base_ami)
            else:
                logging.warn("Test %s has an invalid image %s for linux", test.fullname, env.image)
                return None

        if env.platform.matches.windows:
            if env.image.matches.Dockerfile or env.image.matches.DockerfileInline:
                os = MachineManagement.OsConfig.WindowsWithDocker()
            elif env.image.matches.AMI:
                os = MachineManagement.OsConfig.WindowsVM(env.image.base_ami)
            else:
                logging.warn("Test %s has an invalid image %s for windows", test.fullname, env.image)
                return None

        min_cores = test.testDefinition.min_cores
        max_cores = test.testDefinition.max_cores
        min_ram_gb = test.testDefinition.min_ram_gb

        viable = []

        for hardware in self.machine_management.all_hardware_configs():
            if hardware.cores >= min_cores and hardware.ram_gb >= min_ram_gb:
                viable.append(hardware)

        if not viable:
            logging.warn("Test %s has no viable hardware configurations", test.fullname)
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

        if test.testDefinition.matches.Deployment:
            needed = 0
        elif test.testDefinition.matches.Build:
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
            if dep.dependsOn.totalRuns == 0:
                return True
            if self._testWantsRetries(dep.dependsOn):
                return True
        return False

    def _testHasFailedDeps(self, test):
        for dep in self.database.TestDependency.lookupAll(test=test):
            if dep.dependsOn.totalRuns > 0 and dep.dependsOn.successes == 0 and not self._testWantsRetries(dep.dependsOn):
                return True
        return False

    def _readyToRetryTest(self, test, curTimestamp):
        if test.totalRuns == 0:
            return True

        return curTimestamp - test.lastTestEndTimestamp > test.testDefinition.retry_wait_seconds

    def _testWantsRetries(self, test):
        if test.calculatedPriority == 0:
            return False
        
        if not test.testDefinition.matches.Build:
            return False

        if test.testDefinition.max_retries == 0:
            return False

        if test.successes:
            return False

        if test.totalRuns < test.testDefinition.max_retries:
            return True

        return False

    def _createTest(self, commitData, fullname, testDefinition):
        #make sure it's new
        assert not self.database.Test.lookupAll(fullname=fullname)
        
        test = self.database.Test.New(
            commitData=commitData, 
            fullname=fullname, 
            testDefinition=testDefinition, 
            priority=self.database.TestPriority.NoMoreTests()
            )

        self._checkAllTestDependencies(test)
        self._markTestFullnameCreated(fullname, test)
        self._triggerTestPriorityUpdate(test)

    def _checkAllTestDependencies(self, test):
        commitData = test.commitData

        env = test.testDefinition.environment

        #here we should have a fully populated environment, with all dependencies
        #resolved
        assert env.matches.Environment

        test.machineCategory = self._machineCategoryForTest(test)

        #now we can resolve the test definition, so that we get reasonable dependencies
        dependencies = TestDefinition.apply_test_substitutions(test.testDefinition, env, {}).dependencies

        all_dependencies = dict(env.dependencies)
        all_dependencies.update(test.testDefinition.dependencies)

        #now first check whether this test has any unresolved dependencies
        for depname, dep in all_dependencies.iteritems():
            if dep.matches.ExternalBuild:
                fullname_dep = "/".join([dep.repo, dep.commitHash, dep.name])
                self._createTestDep(test, fullname_dep)
            elif dep.matches.InternalBuild:
                fullname_dep = "/".join([commitData.commit.repo.name, commitData.commit.hash, dep.name])
                self._createTestDep(test, fullname_dep)
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

    def _createTestDep(self, test, fullname_dep):
        dep_test = self.database.Test.lookupAny(fullname=fullname_dep)

        assert dep_test != test

        if not dep_test:
            if self.database.UnresolvedTestDependency.lookupAny(test_and_depends=(test, fullname_dep)) is None:
                self.database.UnresolvedTestDependency.New(test=test, dependsOnName=fullname_dep)
        else:
            if self.database.TestDependency.lookupAny(test_and_depends=(test, dep_test)) is None:
                self.database.TestDependency.New(test=test, dependsOn=dep_test)

    def _markTestFullnameCreated(self, fullname, test_for_name=None):
        for dep in self.database.UnresolvedTestDependency.lookupAll(dependsOnName=fullname):
            test = dep.test
            if test_for_name is not None:
                #this is a real test. If we depended on a commit, this would have been
                #None
                self.database.TestDependency.New(test=test, dependsOn=test_for_name)

            dep.delete()
            self._triggerTestPriorityUpdate(test)
                
    def _triggerCommitPriorityUpdate(self, commit):
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

        if not commit.data:
            self._queueTask(
                self.database.DataTask.New(
                    task=self.database.BackgroundTask.UpdateCommitData(commit=commit),
                    status=pendingHigh
                    )
                )

        return commit

    def _queueTask(self, task):
        existing = self.database.DataTask.lookupAny(status=task.status)

        task.isHead = True

        if existing:
            existing.isHead = False
            task.prior = existing
            task.prior_ct = existing.prior_ct + 1

