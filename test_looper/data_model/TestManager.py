import collections
import logging
import random
import time
import traceback
import simplejson
import threading
from test_looper.core.hash import sha_hash
import test_looper.core.Bitstring as Bitstring
import test_looper.core.object_database as object_database
import test_looper.core.algebraic as algebraic
import test_looper.core.machine_management.MachineManagement as MachineManagement
import test_looper.data_model.Types as Types
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.data_model.TestDefinition as TestDefinition

pendingHigh = Types.BackgroundTaskStatus.PendingHigh()
pendingLow = Types.BackgroundTaskStatus.PendingLow()
running = Types.BackgroundTaskStatus.Running()

MAX_TEST_PRIORITY = 100
TEST_TIMEOUT_SECONDS = 60
IDLE_TIME_BEFORE_SHUTDOWN = 180
MAX_LOG_MESSAGES_PER_TEST = 100000

DISABLE_MACHINE_TERMINATION = False

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



class TestManager(object):
    def __init__(self, source_control, machine_management, kv_store, initialTimestamp=None):
        self.initialTimestamp = initialTimestamp or time.time()

        self.source_control = source_control
        self.machine_management = machine_management

        self.database = object_database.Database(kv_store)
        Types.setup_types(self.database)

        self.writelock = threading.RLock()

        self.heartbeatHandler = HeartbeatHandler()

        self.deploymentStreams = {}

    def commitFindAllBranches(self, commit):
        childCommits = {}

        def children(c):
            toCheck = {}
            for r in self.database.CommitRelationship.lookupAll(parent=c):
                child = r.child

                ix = child.data.parents.index(c)

                toCheck[child] = "^" + str(ix+1) if ix > 1 else "~"

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
            if c not in childCommits or len(path_back) < len(child_commits[c]):
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
            res += test.activeRuns
        return res

    def totalRunningCountForTest(self, test):
        return test.activeRuns

    def toggleBranchUnderTest(self, branch):
        branch.isUnderTest = not branch.isUnderTest
        self._triggerCommitPriorityUpdate(branch.head)


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
            self.database.DataTask.New(
                task=self.database.BackgroundTask.RefreshRepos(), 
                status=pendingHigh
                )

    def markBranchListDirty(self, reponame, curTimestamp):
        with self.transaction_and_lock():
            repo = self.database.Repo.lookupAny(name=reponame)
            assert repo, "Can't find repo named %s" % reponame
            self.database.DataTask.New(
                task=self.database.BackgroundTask.RefreshBranches(repo=repo),
                status=pendingHigh
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
                self._cancelTestRun(testRun, time.time())
                return

            testRun.test.totalRuns = testRun.test.totalRuns - 1
            if testRun.success:
                testRun.test.successes = testRun.test.successes - 1

            testRun.test.totalTestCount = testRun.test.totalTestCount - testRun.totalTestCount
            testRun.test.totalFailedTestCount = testRun.test.totalFailedTestCount - testRun.totalFailedTestCount
            testRun.canceled = True

            self._triggerTestPriorityUpdate(testRun.test)

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

            names = sorted(testSuccesses.keys())
            testRun.testNames = self._testNameSet(names)
            testRun.testFailures = Bitstring.Bitstring.fromBools([testSuccesses[n] for n in names])
            testRun.totalTestCount = len(names)
            testRun.totalFailedTestCount = len([n for n in names if not testSuccesses[n]])

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

        logging.info('test %s heartbeating', testId)

        with self.transaction_and_lock():
            testRun = self.database.TestRun(str(testId))

            if not testRun.exists():
                return False

            if testRun.canceled:
                return False

            if not testRun.machine.isAlive:
                logging.error("Test %s heartbeat, but machine %s is dead!", testId, testRun.machine.machineId)

                self._cancelTestRun(testRun, timestamp)
                return False
            else:
                self._machineHeartbeat(testRun.machine, timestamp)

                testRun.lastHeartbeat = timestamp

                return True

    def _lookupHighestPriorityTest(self, machine):
        t0 = time.time()

        count = 0

        for priorityType in [
                self.database.TestPriority.FirstBuild,
                self.database.TestPriority.FirstTest,
                self.database.TestPriority.WantsMoreTests
                ]:
            for priority in reversed(range(1,MAX_TEST_PRIORITY+1)):
                for test in self.database.Test.lookupAll(priority=priorityType(priority)):
                    count += 1
                    if time.time() - t0 > .1:
                        logging.info("Checking priority %s", priorityType(priority))

                    if self._machineCategoryPairForTest(test) == (machine.hardware, machine.os):
                        return test

    def startNewDeployment(self, machineId, timestamp):
        """Allocates a new test and returns (repoName, commitHash, testName, deploymentId) or (None,None,None, None) if no work."""
        with self.transaction_and_lock():
            machine = self.database.Machine.lookupAny(machineId=machineId)

            if machine is None or not machine.isAlive:
                logging.warn("Can't assign work to a machine we don't know about: %s", machineId)
                return None, None, None, None

            self._machineHeartbeat(machine, timestamp)

            for deployment in self.database.Deployment.lookupAll(isAliveAndPending=True):
                if self._machineCategoryForTest(deployment.test) == self._machineCategoryForPair(machine.hardware, machine.os):
                    deployment.machine = machine
                    
                    self.streamForDeployment(deployment._identity).addMessageFromDeployment(
                        time.asctime(time.gmtime(timestamp)) + 
                            " TestLooper> Machine %s accepting deployment.\n\r" % machineId
                        )
                    
                    test = deployment.test

                    return (test.commitData.commit.repo.name, test.commitData.commit.hash, test.testDefinition.name, deployment._identity)

            return None, None, None, None

    def isDeployment(self, deploymentId):
        with self.database.view():
            return self.database.Deployment(deploymentId).exists()


    def handleDeploymentConnectionReinitialized(self, deploymentId, timestamp, allLogs):
        self.streamForDeployment(deploymentId).allMessagesFromDeploymentFromStart(deploymentId, timestamp, allLogs)

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
        """Allocates a new test and returns (repoName, commitHash, testName, testId) or (None,None,None, None) if no work."""
        with self.transaction_and_lock():
            machine = self.database.Machine.lookupAny(machineId=machineId)

            if not machine or not machine.isAlive:
                return None, None, None, None

            self._machineHeartbeat(machine, timestamp)

        with self.transaction_and_lock():
            t0 = time.time()
            test = self._lookupHighestPriorityTest(machine)
            if time.time() - t0 > .25:
                logging.warn("Took %s to get priority", time.time() - t0)

            if not test:
                return None, None, None, None

            test.activeRuns = test.activeRuns + 1

            machine = self.database.Machine.lookupOne(machineId=machineId)

            runningTest = self.database.TestRun.New(
                test=test,
                startedTimestamp=timestamp,
                lastHeartbeat=timestamp,
                machine=machine
                )

            self._updateTestPriority(test, timestamp)

            return (test.commitData.commit.repo.name, test.commitData.commit.hash, test.testDefinition.name, runningTest._identity)

    def performCleanupTasks(self, curTimestamp):
        #check all tests to see if we've exceeded the timeout and the test is dead
        with self.transaction_and_lock():
            for t in self.database.TestRun.lookupAll(isRunning=True):
                if t.lastHeartbeat < curTimestamp - TEST_TIMEOUT_SECONDS and curTimestamp - self.initialTimestamp > TEST_TIMEOUT_SECONDS:
                    self._cancelTestRun(t, curTimestamp)

        with self.transaction_and_lock():
            self._scheduleBootCheck()
            self._shutdownMachinesIfNecessary(curTimestamp)
            
    def _scheduleBootCheck(self):
        if not self.database.DataTask.lookupAny(pending_boot_machine_check=True):
            self.database.DataTask.New(
                task=self.database.BackgroundTask.BootMachineCheck(),
                status=pendingHigh
                )

    def _cancelTestRun(self, testRun, curTimestamp):
        assert testRun.endTimestamp == 0.0

        testRun.canceled = True
        testRun.test.activeRuns = testRun.test.activeRuns - 1
        self.heartbeatHandler.testFinished(testRun.test._identity)
    
        os = testRun.machine.os

        if (os.matches.WindowsVM or os.matches.LinuxVM):
            #we need to shut down this machine since it has a setup script
            if not DISABLE_MACHINE_TERMINATION:
                self._terminateMachine(testRun.machine, curTimestamp)

        self._triggerTestPriorityUpdate(testRun.test)

    def performBackgroundWork(self, curTimestamp):
        with self.transaction_and_lock():
            task = self.database.DataTask.lookupAny(status=pendingHigh)
            if task is None:
                task = self.database.DataTask.lookupAny(status=pendingLow)

            if task is None:
                return
                
            task.status = running

            testDef = task.task

        try:
            self._processTask(testDef, curTimestamp)
        except:
            traceback.print_exc()
            logging.error("Exception processing task %s:\n\n%s", testDef, traceback.format_exc())
        finally:
            with self.transaction_and_lock():
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
            all_repos = set(self.source_control.listRepos())

            with self.transaction_and_lock():
                repos = self.database.Repo.lookupAll(isActive=True)

                for r in repos:
                    if r.name not in all_repos:
                        r.delete()

                existing = set([x.name for x in repos])

                for new_repo_name in all_repos - existing:
                    r = self._createRepo(new_repo_name)

                for r in self.database.Repo.lookupAll(isActive=True):
                    self.database.DataTask.New(
                        task=self.database.BackgroundTask.RefreshBranches(r),
                        status=pendingHigh
                        )

        elif task.matches.RefreshBranches:
            with self.transaction_and_lock():
                repo = self.source_control.getRepo(task.repo.name)
                repo.source_repo.fetchOrigin()

                branchnames = repo.listBranches()

                branchnames_set = set(branchnames)

                db_repo = task.repo

                db_branches = self.database.Branch.lookupAll(repo=db_repo)

                logging.info(
                    "Comparing branchlist from server: %s to local: %s", 
                    sorted(branchnames_set), 
                    sorted([x.branchname for x in db_branches])
                    )

                final_branches = tuple([x for x in db_branches if x.branchname in branchnames_set])
                for branch in db_branches:
                    if branch.branchname not in branchnames_set:
                        branch.delete()

                for newname in branchnames_set - set([x.branchname for x in db_branches]):
                    newbranch = self.database.Branch.New(branchname=newname, repo=db_repo)

                for branchname in branchnames:
                    try:
                        self.database.DataTask.New(
                            task=self.database.BackgroundTask.UpdateBranchTopCommit(
                                self.database.Branch.lookupOne(reponame_and_branchname=(db_repo.name, branchname))
                                ),
                            status=pendingHigh
                            )
                    except:
                        logging.error("Error scheduling branch commit lookup:\n\n%s", traceback.format_exc())

        elif task.matches.UpdateBranchTopCommit:
            with self.transaction_and_lock():
                repo = self.source_control.getRepo(task.branch.repo.name)
                commit = repo.branchTopCommit(task.branch.branchname)

                logging.info('Top commit of %s branch %s is %s', repo, task.branch.branchname, commit)

                if commit:
                    if task.branch.head:
                        self._triggerCommitPriorityUpdate(task.branch.head)
                    task.branch.head = self._lookupCommitByHash(task.branch.repo, commit)
                    if task.branch.head:
                        self._triggerCommitPriorityUpdate(task.branch.head)

        elif task.matches.UpdateCommitData:
            with self.transaction_and_lock():
                repo = self.source_control.getRepo(task.commit.repo.name)
                
                commit = task.commit

                if commit.data is self.database.CommitData.Null:
                    if not repo.source_repo.commitExists(commit.hash):
                        return

                    hashParentsAndTitle = repo.commitsLookingBack(commit.hash, 1)[0]

                    subject=hashParentsAndTitle[2]
                    parents=[
                        self._lookupCommitByHash(task.commit.repo, p) 
                            for p in hashParentsAndTitle[1]
                        ]
                    
                    commit.data = self.database.CommitData.New(
                        commit=commit,
                        subject=subject,
                        parents=parents
                        )
                    for p in parents:
                        self.database.CommitRelationship.New(child=commit,parent=p)

                    self._triggerCommitPriorityUpdate(commit)

                    try:
                        defText, extension = repo.getTestScriptDefinitionsForCommit(task.commit.hash)
                        
                        if defText is None:
                            raise Exception("No test definition file found.")

                        all_tests, all_environments = TestDefinitionScript.extract_tests_from_str(commit.repo.name, commit.hash, extension, defText)

                        commit.data.testDefinitions = all_tests
                        commit.data.environments = all_environments
                        
                        for e in all_tests.values():
                            fullname=commit.repo.name + "/" + commit.hash + "/" + e.name

                            self._createTest(
                                commitData=commit.data,
                                fullname=fullname,
                                testDefinition=e
                                )

                    except Exception as e:
                        if not str(e):
                            logging.error("%s", traceback.format_exc())

                        logging.warn("Got an error parsing tests for %s/%s:\n%s", commit.repo.name, commit.hash, traceback.format_exc())

                        commit.data.testDefinitionsError=str(e)

        elif task.matches.UpdateTestPriority:
            with self.transaction_and_lock():
                self._updateTestPriority(task.test, curTimestamp)
        elif task.matches.BootMachineCheck:
            with self.transaction_and_lock():
                self._bootMachinesIfNecessary(curTimestamp)
        elif task.matches.UpdateCommitPriority:
            with self.transaction_and_lock():
                self._updateCommitPriority(task.commit)
        else:
            raise Exception("Unknown task: %s" % task)

    def _bootMachinesIfNecessary(self, curTimestamp):
        #repeatedly check if we can boot any machines. If we can't,
        #but we want to, we need to check whether there are any machines we can
        #shut down
        logging.info("Entering _bootMachinesIfNecessary:")
        for cat in (self.database.MachineCategory.lookupAll(want_more=True) +  
                            self.database.MachineCategory.lookupAll(want_less=True)):
            logging.info("\t%s/%s: %s vs %s", cat.hardware, cat.os, cat.desired, cat.booted)

        def check():
            wantingBoot = self.database.MachineCategory.lookupAll(want_more=True)
            wantingShutdown = self.database.MachineCategory.lookupAll(want_less=True)

            def canBoot():
                for c in wantingBoot:
                    if self.machine_management.canBoot(c.hardware, c.os):
                        return c

            while wantingBoot and not canBoot() and wantingShutdown:
                self._shutdown(wantingShutdown[0], curTimestamp, onlyIdle=False)

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

        return curTimestamp - machine.lastTestCompleted > IDLE_TIME_BEFORE_SHUTDOWN

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
            for cat in self.database.MachineCategory.lookupAll(want_less=True):
                if cat.desired < cat.booted:
                    return self._shutdown(cat, curTimestamp, onlyIdle=True)

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

    def _updateCommitPriority(self, commit):
        priority = self._computeCommitPriority(commit)
        
        logging.debug("Commit %s/%s has new priority %s%s%s", 
            commit.repo.name, 
            commit.hash, 
            priority,
            "==" if priority == commit.priority else "!=",
            commit.priority
            )

        if priority != commit.priority:
            commit.priority = priority

            if commit.data:
                for p in commit.data.parents:
                    self._triggerCommitPriorityUpdate(p)

                for test in self.database.Test.lookupAll(commitData=commit.data):
                    for dep in self.database.TestDependency.lookupAll(test=test):
                        if commit != dep.dependsOn.commitData.commit:
                            self._triggerCommitPriorityUpdate(dep.dependsOn.commitData.commit)
                
                for test in self.database.Test.lookupAll(commitData=commit.data):
                    self._triggerTestPriorityUpdateIfNecessary(test)

    def _computeCommitPriority(self, commit):
        priority = 0

        for b in self.database.Branch.lookupAll(head=commit):
            if b.isUnderTest:
                logging.info("Commit %s/%s enabled because branch %s under test.", commit.repo.name, commit.hash, b.branchname)
                priority = MAX_TEST_PRIORITY

        for relationship in self.database.CommitRelationship.lookupAll(parent=commit):
            priority = max(priority, relationship.child.priority - 1)

        return priority

    def _updateTestPriority(self, test, curTimestamp):
        self._checkAllTestDependencies(test)
        
        #cancel any runs already going if this gets deprioritized
        if test.commitData.commit.priority == 0:
            for run in self.database.TestRun.lookupAll(test=test):
                if run.endTimestamp == 0.0 and not run.canceled:
                    self._cancelTestRun(run, curTimestamp)

        oldPriority = test.priority
        oldTargetMachineBoot = test.targetMachineBoot

        category = test.machineCategory

        if test.fullyResolvedEnvironment.matches.Error:
            test.priority = self.database.TestPriority.InvalidTestDefinition()
            test.targetMachineBoot = 0
        elif category and category.hardwareComboUnbootable:
            test.priority = self.database.TestPriority.HardwareComboUnbootable()
            test.targetMachineBoot = 0
        elif (self.database.UnresolvedTestDependency.lookupAll(test=test) or 
                self.database.UnresolvedRepoDependency.lookupAll(test=test) or 
                self.database.UnresolvedSourceDependency.lookupAll(test=test)):
            test.priority = self.database.TestPriority.UnresolvedDependencies()
        elif self._testHasUnfinishedDeps(test):
            test.priority = self.database.TestPriority.WaitingOnBuilds()
        elif self._testHasFailedDeps(test):
            test.priority = self.database.TestPriority.DependencyFailed()
        else:
            #sets test.targetMachineBoot
            if not test.testDefinition.matches.Deployment and test.testDefinition.disabled:
                test.priority = self.database.TestPriority.NoMoreTests()
            elif self._updateTestTargetMachineCountAndReturnIsDone(test):
                test.priority = self.database.TestPriority.NoMoreTests()
            elif test.testDefinition.matches.Build:
                test.priority = self.database.TestPriority.FirstBuild(priority=test.commitData.commit.priority)
            elif (test.totalRuns + test.activeRuns) == 0:
                test.priority = self.database.TestPriority.FirstTest(priority=test.commitData.commit.priority)
            else:
                test.priority = self.database.TestPriority.WantsMoreTests(priority=test.commitData.commit.priority)

        if category:
            net_change = test.targetMachineBoot - oldTargetMachineBoot

            if net_change != 0:
                category.desired = category.desired + net_change
                self._scheduleBootCheck()

    def _checkMachineCategoryCounts(self):
        desired = {}
        booted = {}

        for machine in self.database.Machine.lookupAll(isAlive=True):
            cat = self._machineCategoryForPair(machine.hardware, machine.os)
            if cat not in desired:
                desired[cat] = 0
            if cat not in booted:
                booted[cat] = 0
            booted[cat] += 1

        for cat in self.database.MachineCategory.lookupAll(want_less=True):
            if cat not in desired:
                desired[cat] = 0

        for cat in self.database.MachineCategory.lookupAll(want_more=True):
            if cat not in desired:
                desired[cat] = 0

        def checkTestCategory(test):
            real_cat = self._machineCategoryForTest(test)

            if real_cat != test.machineCategory:
                logging.warn("test %s had incorrect desired machine category %s != %s", 
                    test.fullname, 
                    str(test.machineCategory.hardware) + "/" + str(test.machineCategory.os),
                    str(real_cat.hardware) + "/" + str(real_cat.os)
                    )

                test.machineCategory = real_cat            

        for testRun in self.database.TestRun.lookupAll(isRunning=True):
            cat = testRun.test.machineCategory
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
                    if cat not in desired:
                        desired[cat] = 0
                    desired[cat] += 1

        for deployment in self.database.Deployment.lookupAll(isAlive=True):
            checkTestCategory(deployment.test)

            cat = deployment.test.machineCategory
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
        if not test.fullyResolvedEnvironment.matches.Resolved:
            return None

        env = test.fullyResolvedEnvironment.Environment

        if env.platform.matches.linux:
            if env.image.matches.Dockerfile or env.image.matches.DockerfileInline:
                os = MachineManagement.OsConfig.LinuxWithDocker()
            elif env.image.matches.AMI:
                os = MachineManagement.OsConfig.LinuxVM(env.image.base_ami)
            else:
                return None

        if env.platform.matches.windows:
            if env.image.matches.Dockerfile or env.image.matches.DockerfileInline:
                os = MachineManagement.OsConfig.WindowsWithDocker()
            elif env.image.matches.AMI:
                os = MachineManagement.OsConfig.WindowsVM(env.image.base_ami)
            else:
                return None

        min_cores = test.testDefinition.min_cores
        max_cores = test.testDefinition.max_cores
        min_ram_gb = test.testDefinition.min_ram_gb

        viable = []

        for hardware in self.machine_management.all_hardware_configs():
            if hardware.cores >= min_cores and hardware.ram_gb >= min_ram_gb:
                viable.append(hardware)

        if not viable:
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

    def _updateTestTargetMachineCountAndReturnIsDone(self, test):
        if test.commitData.commit.priority == 0:
            test.targetMachineBoot = 0
            return True

        if test.testDefinition.matches.Deployment:
            needed = 0
        elif test.testDefinition.matches.Build:
            needed = 1
        else:
            needed = max(test.runsDesired, 1)

        test.targetMachineBoot = max(needed - test.totalRuns, 0)

        return test.totalRuns + test.activeRuns >= needed

    def _testHasUnfinishedDeps(self, test):
        deps = self.database.TestDependency.lookupAll(test=test)
        unresolved_deps = self.database.UnresolvedTestDependency.lookupAll(test=test)

        for dep in deps:
            if dep.dependsOn.totalRuns == 0:
                return True
        return False

    def _testHasFailedDeps(self, test):
        for dep in self.database.TestDependency.lookupAll(test=test):
            if dep.dependsOn.successes == 0:
                return True
        return False

    def _createTest(self, commitData, fullname, testDefinition):
        #make sure it's new
        assert not self.database.Test.lookupAll(fullname=fullname)
        
        test = self.database.Test.New(
            commitData=commitData, 
            fullname=fullname, 
            testDefinition=testDefinition, 
            fullyResolvedEnvironment=self.database.FullyResolvedTestEnvironment.Unresolved(),
            priority=self.database.TestPriority.UnresolvedDependencies()
            )

        self._checkAllTestDependencies(test)
        self._markTestFullnameCreated(fullname, test)
        self._triggerTestPriorityUpdateIfNecessary(test)

    def _checkAllTestDependencies(self, test):
        commitData = test.commitData

        env = test.testDefinition.environment

        try:
            while env is not None and env.matches.Import:
                imported_envs = []

                for dep in env.imports:
                    commit = self._lookupCommitByHash(dep.repo, dep.commitHash)
                    self._createSourceDep(test, dep.repo, dep.commitHash)

                    if commit and commit.data:
                        #this dependency exists already
                        underlying_env = commit.data.environments.get(dep.name, None)
                        
                        if underlying_env is not None:
                            imported_envs.append(underlying_env)
                        else:
                            env = None
                    else:
                        env = None

                if env is not None:
                    env = TestDefinition.merge_environments(env, imported_envs)

            if env is None or env.matches.Import:
                return

            env = TestDefinition.apply_environment_substitutions(env)
        except Exception as e:
            test.fullyResolvedEnvironment = self.database.FullyResolvedTestEnvironment.Error(Error=str(e))
            return

        #here we should have a fully populated environment, with all dependencies
        #resolved
        assert env.matches.Environment

        test.fullyResolvedEnvironment = self.database.FullyResolvedTestEnvironment.Resolved(env)
        test.machineCategory = self._machineCategoryForTest(test)

        #now we can resolve the test definition, so that we get reasonable dependencies
        dependencies = TestDefinition.apply_test_substitutions(test.testDefinition, env, {}).dependencies

        all_dependencies = dict(env.dependencies)
        all_dependencies.update(test.testDefinition.dependencies)

        #now first check whether this test has any unresolved dependencies
        for depname, dep in all_dependencies.iteritems():
            if dep.matches.ExternalBuild:
                fullname_dep = "/".join([dep.repo, dep.commitHash, dep.name, dep.environment])
                self._createTestDep(test, fullname_dep)
            elif dep.matches.InternalBuild:
                fullname_dep = "/".join([commitData.commit.repo.name, commitData.commit.hash, dep.name, dep.environment])
                self._createTestDep(test, fullname_dep)
            elif dep.matches.Source:
                self._createSourceDep(test, dep.repo, dep.commitHash)



    def _createSourceDep(self, test, reponame, commitHash):
        repo = self.database.Repo.lookupAny(name=reponame)
        if not repo:
            if self.database.UnresolvedRepoDependency.lookupAny(test_and_reponame=(test, reponame)) is None:
                self.database.UnresolvedRepoDependency.New(test=test,reponame=reponame, commitHash=commitHash)
            return True

        commit = self.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))
        if not commit:
            assert commitHash
            if self.database.UnresolvedSourceDependency.lookupAny(test_and_repo_and_hash=(test, repo, commitHash)) is None:
                self.database.UnresolvedSourceDependency.New(test=test, repo=repo, commitHash=commitHash)
            return True

        return False

    def _createTestDep(self, test, fullname_dep):
        dep_test = self.database.Test.lookupAny(fullname=fullname_dep)

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
            self._triggerTestPriorityUpdateIfNecessary(test)
                
    def _triggerCommitPriorityUpdate(self, commit):
        self.database.DataTask.New(
            task=self.database.BackgroundTask.UpdateCommitPriority(commit=commit),
            status=pendingHigh
            )

    def _triggerTestPriorityUpdateIfNecessary(self, test):
        if not (self.database.UnresolvedTestDependency.lookupAll(test=test) + 
                self.database.UnresolvedRepoDependency.lookupAll(test=test) +
                self.database.UnresolvedSourceDependency.lookupAll(test=test)
                ):
            self._triggerTestPriorityUpdate(test)

    def _triggerTestPriorityUpdate(self, test):
        #test priority updates are always 'low' because we want to ensure
        #that all commit updates have triggered first. This way we know that
        #we're not accidentally going to cancel a test
        self.database.DataTask.New(
            task=self.database.BackgroundTask.UpdateTestPriority(test=test),
            status=pendingLow
            )

    def _createRepo(self, new_repo_name):
        r = self.database.Repo.New(name=new_repo_name,isActive=True)

        for dep in self.database.UnresolvedRepoDependency.lookupAll(reponame=new_repo_name):
            self._createSourceDep(dep.test, new_repo_name, dep.commitHash)
            test = dep.test

            #delete this first, since we check to see if any such dependencies exist!
            dep.delete()

            self._triggerTestPriorityUpdateIfNecessary(test)

        return r

    def _lookupCommitByHash(self, repo, commitHash):
        if isinstance(repo, str):
            repoName = repo
            repo = self.database.Repo.lookupAny(name=repo)
            if not repo:
                logging.warn("Unknown repo %s while looking up %s/%s", repo, repoName, commitHash)
                return None

        commit = self.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))

        if not commit:
            commit = self.database.Commit.New(repo=repo, hash=commitHash)

            self.database.DataTask.New(
                task=self.database.BackgroundTask.UpdateCommitData(commit=commit),
                status=pendingHigh
                )

            for dep in self.database.UnresolvedSourceDependency.lookupAll(repo_and_hash=(repo, commitHash)):
                test = dep.test
                dep.delete()
                self._triggerTestPriorityUpdateIfNecessary(test)


        return commit
