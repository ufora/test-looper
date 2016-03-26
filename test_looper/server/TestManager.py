import collections
import logging
import random
import time

import test_looper.core.TestResult as TestResult
import test_looper.core.TestScriptDefinition as TestScriptDefinition

import test_looper.server.BlockingMachines as BlockingMachines
import test_looper.server.Branch as Branch
import test_looper.server.Commit as Commit
import test_looper.server.CommitAndTestToRun as CommitAndTestToRun

def pad(s, length):
    if len(s) < length:
        return s + " " * (length - len(s))
    return s[:length]


class TestDatabase(object):
    def __init__(self, kvStore):
        self.kvStore = kvStore
        self.dbPrefix = "1_"

    def getTestIdsForCommit(self, commitId):
        tests = self.kvStore.get(self.dbPrefix + "commit_tests_" + commitId)

        if tests:
            return tests
        return []

    def loadTestResultForTestId(self, testId):
        res = self.kvStore.get(self.dbPrefix + "test_" + testId)
        if not res:
            return res

        return TestResult.TestResult.fromJson(res)

    def clearResultsForTestIdCommitId(self, testId, commitId):
        self.kvStore.delete(self.dbPrefix + "test_" + testId)
        testIds = self.kvStore.get(self.dbPrefix + "commit_tests_" + commitId)
        if testIds is None:
            return
        filtered = [testId for testId in testIds if testId != testId]
        self.kvStore.set(self.dbPrefix + "commit_tests_" + commitId, filtered)


    def clearAllTestsForCommitId(self, commitId):
        ids = self.getTestIdsForCommit(commitId)

        for testId in ids:
            self.kvStore.delete(self.dbPrefix + "test_" + testId)

        self.kvStore.delete(self.dbPrefix + "commit_tests_" + commitId)

    def updateTestListForCommit(self, commit):
        ids = sorted(commit.testsById.keys())

        self.kvStore.set(self.dbPrefix + "commit_tests_" + commit.commitId, ids)

    def updateTestResult(self, result):
        self.kvStore.set(self.dbPrefix + "test_" + result.testId, result.toJson())

    def getTestScriptDefinitionsForCommit(self, commitId):
        res = self.kvStore.get("commit_test_definitions_" + commitId)
        if res is None:
            return None

        return [TestScriptDefinition.TestScriptDefinition.fromJson(x) for x in res]

    def setTestScriptDefinitionsForCommit(self, commit, result):
        self.kvStore.set("commit_test_definitions_" + commit, [x.toJson() for x in result])

    def getTargetedTestTypesForBranch(self, branchname):
        return self.kvStore.get("branch_targeted_tests_" + branchname) or []

    def setTargetedTestTypesForBranch(self, branchname, testNames):
        return self.kvStore.set("branch_targeted_tests_" + branchname, testNames)

    def getTargetedCommitIdsForBranch(self, branchname):
        return self.kvStore.get("branch_targeted_commit_ids_" + branchname) or []

    def setTargetedCommitIdsForBranch(self, branchname, commitIds):
        return self.kvStore.set("branch_targeted_commit_ids_" + branchname, commitIds)

    def getBranchIsDeepTestBranch(self, branchname):
        result = self.kvStore.get("branch_is_deep_test_" + branchname)
        if result is None:
            if branchname == "origin/master":
                return True
            else:
                return False
        return result

    def setBranchIsDeepTestBranch(self, branchname, isDeep):
        return self.kvStore.set("branch_is_deep_test_" + branchname, isDeep)


TestManagerSettings = collections.namedtuple(
    'TestManagerSettings',
    'baseline_branch baseline_depth builder_min_cores'
    )


class TestManager(object):
    VERSION = "0.0.1"

    def __init__(self, github, kvStore, lock, settings):
        self.github = github
        self.kvStore = kvStore
        self.testDb = TestDatabase(kvStore)
        self.settings = settings

        self.mostRecentTouchByMachine = {}
        self.branches = {}
        self.commits = {}
        self.periodicTestRunBranchCandidates = ['origin/master']

        #dict from internalIp to properties of blocking machines
        self.blockingMachines = BlockingMachines.BlockingMachines()
        self.lock = lock

    def clearResultsForTestIdCommitId(self, testId, commitId):
        self.testDb.clearResultsForTestIdCommitId(testId, commitId)
        commit = self.commits[commitId]
        test = commit.testsById[testId]
        commit.clearTestResult(test.testName, testId)

    def machineRequestedTest(self, machineId):
        self.mostRecentTouchByMachine[machineId] = time.time()

    def refresh(self, lock=None):
        self.updateBranchesUnderTest(lock)

    def initialize(self):
        self.updateBranchesUnderTest()
        self.loadTestResults(self.commits)

    def getCommitByCommitId(self, commitId):
        if not commitId in self.commits:
            revList = "%s ^%s^^" % (commitId, commitId)
            commitId, parentHash, commitTitle = \
                self.github.commitIdsParentHashesAndSubjectsInRevlist(revList)[0]
            self.commits[commitId] = self.createCommit(commitId, parentHash, commitTitle)
        return self.commits[commitId]


    def getTestById(self, testId):
        #we need to add indices to this object, so that this can be fast
        for c in self.commits.values():
            for t in c.testsById:
                if t == testId:
                    return c.testsById[t]
        return None

    def clearCommitId(self, commitId):
        "Remove all test-runs associated with 'commitId'"""
        self.testDb.clearAllTestsForCommitId(commitId)
        self.commits[commitId].clearTestResults()

    def distinctBranches(self):
        return set(self.branches.keys())

    def commitsInBranch(self, branchName):
        return self.branches[branchName].commits.values()

    def getPeriodicTestsToRun(self):
        result = []
        for branch in self.branches.values():
            if branch.branchName in self.periodicTestRunBranchCandidates and \
                    len(branch.commitsInOrder) > 0:
                mostRecentCommit = branch.commitsInOrder[0]
                if mostRecentCommit.needsBuild():
                    logging.info("%s needs build, branch: %s", mostRecentCommit, branch)
                    continue
                periodicTests = [
                    t for t in mostRecentCommit.testScriptDefinitions if t.periodicTest
                    ]
                for periodicTest in periodicTests:
                    lastTestRunStarted = mostRecentCommit.lastTestRunStarted(periodicTest.testName)
                    if lastTestRunStarted is not None:
                        logging.warn(
                            "Test: %s, last run: %s, diff: %s, test period: %s",
                            periodicTest,
                            lastTestRunStarted,
                            time.time() - lastTestRunStarted,
                            periodicTest.periodicTestPeriodInHours * 60 * 60
                            )
                    if lastTestRunStarted is None or \
                            time.time() - lastTestRunStarted > (periodicTest.periodicTestPeriodInHours * 60 * 60):
                        result.append((mostRecentCommit, periodicTest.testName))

        logging.info("Get periodic tests to run: %s", result)
        return result

    def canMachineBuild(self, workerInfo):
        return workerInfo.coreCount >= self.settings.builder_min_cores if workerInfo else True

    def getPossibleCommitsAndTests(self, workerInfo=None, includePeriodicTests=True):
        """Return a list consisting of all possible commit/test combinations we'd consider running.

        Each item the list is a tuple

            (commit, test)

        where commit is a Commit object and 'test' is either a string giving the test name or None
        indicating that we don't know the list of commits.
        """
        result = []

        if includePeriodicTests:
            result += self.getPeriodicTestsToRun()

        for commit in self.commits.itervalues():
            if (commit.excludeFromTestingBecauseOfCommitSubject() or
                    commit.buildInProgress() or commit.isBrokenBuild() or
                    not commit.isDeepTest):
                continue

            if commit.needsBuild() and self.canMachineBuild(workerInfo):
                testDef = commit.getTestDefinitionFor('build')
                logging.info("testDef for build: %s", testDef)
                if (testDef is not None and (
                        workerInfo is None or
                        self.blockingMachines.machineCanParticipateInTest(testDef,
                                                                          workerInfo))):
                    result.append((commit, 'build'))
            elif not commit.needsBuild():
                result += [
                    (commit, testName) for testName in commit.statsByType.iterkeys()
                    if (testName != 'build' and
                        not commit.getTestDefinitionFor(testName).periodicTest and
                        (workerInfo is None or
                         self.blockingMachines.machineCanParticipateInTest(
                             commit.getTestDefinitionFor(testName),
                             workerInfo)))
                    ]

        return result

    # we need the current number of provisioned machines because we don't want to run
    # any unit tests that require n machines unless we have that number of machines already
    def getTask(self, workerInfo):
        """
        We are passed the current number of provisioned machines so we don't try to assign
        to a unit test whose target machine count can't be satisfied
        """
        t0 = time.time()
        result = self.getTask_(workerInfo)

        logging.info("calling getTask_ took %s seconds and returned %s",
                     time.time() - t0, result)

        return result

    def hasPendingPeriodicTests(self):
        periodic = self.getPeriodicTestsToRun()
        commitLevelDict = self.computeCommitLevels()
        for (commit, testName) in periodic:
            score = self.scoreCommitAndTest(commitLevelDict, commit, testName)
            if score > 0:
                return True
        return False

    def getTask_(self, workerInfo):
        """This method is called from a worker machine looking for a test to run. We
        will give him the highest priority test that he is qualified to run

        We are passed the current number of provisioned machines so we don't try to assign
        to a unit test whose target machine count can't be satisfied
        """

        allCommitsToTest = self.getPossibleCommitsAndTests(workerInfo)
        candidates = self.prioritizeCommitsAndTests(allCommitsToTest)

        if not candidates:
            logging.info("Prioritized commits and tests returned none")
            return None, None, None


        firstCandidate = candidates[0]
        commit = firstCandidate.commit
        testName = firstCandidate.testName

        testDefinition = commit.getTestDefinitionFor(testName)
        logging.info("Next test definition: %s", testDefinition)
        assert testDefinition is not None, \
            "Couldn't find %s within tests %s in commit %s. testDefs are %s" % (
                testName,
                commit.statsByType.keys(),
                commit.commitId,
                commit.testScriptDefinitions.keys()
                )

        testResult = self.blockingMachines.getTestAssignment(commit,
                                                             testName,
                                                             workerInfo)

        if testResult is None:
            logging.warn("Test result is none")
            return None, None, None

        if testResult.commitId != commit.commitId:
            commit = self.commits[testResult.commitId]

        if testResult.testId not in commit.testsById:
            commit.addTestResult(testResult, updateDB=True)
            self.testDb.updateTestResult(testResult)

        return commit, commit.getTestDefinitionFor(testResult.testName), testResult


    def heartbeat(self, testId, commitId, machineId):
        self.mostRecentTouchByMachine[machineId] = time.time()
        if commitId in self.commits:
            commit = self.commits[commitId]
            return commit.heartbeatTest(testId, machineId)
        else:
            logging.warn("Got a heartbeat for commit %s which I don't know about", commitId)
            return TestResult.TestResult.HEARTBEAT_RESPONSE_DONE

    def recordMachineResult(self, result):
        commitId = result.commitId
        testId = result.testId
        if not commitId in self.commits:
            logging.warn("Commit id %s not found in test manager commits", commitId)
            return
        test = self.commits[commitId].testsById[testId]
        self.commits[commitId].testChanged(test.testName)

        test.recordMachineResult(result)
        self.testDb.updateTestResult(test)
        self.mostRecentTouchByMachine[result.machine] = time.time()

    def computeCommitLevels(self):
        """Given a set of Commit objects, produce a dictionary from commitId to "level",
        where 'level' is 0 for leaf commits and increases by 1 at each parent."""
        commitLevel = {}
        commitsById = {}
        commits = list(self.commits.values())

        for c in commits:
            commitsById[c.commitId] = c

        parentIds = set([c.parentId for c in commits])
        leaves = set([c for c in commits if c.commitId not in parentIds])

        def followChain(commit, level):
            if commit.commitId not in commitLevel or commitLevel[commit.commitId] > level:
                commitLevel[commit.commitId] = level

                if commit.parentId in commitsById:
                    followChain(commitsById[commit.parentId], level+1)

        for l in leaves:
            followChain(l, 0)

        return commitLevel

    def prioritizeCommitsAndTests(self, candidates, preferTargetedTests=True):
        """
        Return a list of (commit, testName, priority) sorted by priority.

        candidates - a list of (commit, testName) pairs

        The returned list is a subset of candidates ordered by preference, with most preferable
        first in the list.
        """
        if preferTargetedTests:
            targetedCandidates = [c for c in candidates if c[0].isTargetedCommitAndTest(c[1])]
            if len(targetedCandidates) > 0 and random.random() < 0.5:
                candidates = targetedCandidates

        commitLevelDict = self.computeCommitLevels()

        def scoreCommitAndTest(candidate):
            return self.scoreCommitAndTest(commitLevelDict, candidate[0], candidate[1])


        commitsAndTestsToRun = map(
            lambda candidate: CommitAndTestToRun.CommitAndTestToRun(
                candidate[1],
                candidate[0],
                scoreCommitAndTest(candidate)
                ),
            candidates
            )

        return sorted(commitsAndTestsToRun, key=lambda c: c.priority, reverse=True)

    BASE_PRIORITY_UNKNOWN_COMMIT       = 10000000000000
    BASE_PRIORITY_UNBUILT_COMMIT       = 1000000000
    BASE_PRIORITY_PERIODIC_TEST_COMMIT = 1000000
    BASE_PRIORITY_UNTESTED_COMMIT      = 100000
    BASE_PRIORITY_TARGETED_COMMIT      = 1000

    def scoreCommitAndTest(self, commitLevelDict, commit, testName):
        """Returns the priority score for this commit"""
        commitLevel = commitLevelDict[commit.commitId]

        #this is a log-probability measure of how 'suspicious' this commit is
        suspiciousness = min(commit.suspiciousnessLevelForTest(testName), 10)

        #note that we use a smaller power than "e" even though it's log probability. This compresses
        #the spread of the tests so that we don't focus too much
        weightPerNonTimedOutRun = 1 / (.5 + 1.5 ** suspiciousness) / 5.0
        if testName is None:
            return self.BASE_PRIORITY_UNKNOWN_COMMIT - commitLevel
        if testName == "build":
            return self.BASE_PRIORITY_UNBUILT_COMMIT - commitLevel
        if commit.isPeriodicTest(testName):
            return self.BASE_PRIORITY_PERIODIC_TEST_COMMIT - commitLevel
        if commit.totalNonTimedOutRuns(testName) == 0:
            return self.BASE_PRIORITY_UNTESTED_COMMIT - commitLevel / 10000.0
        if commit.isTargetedCommitAndTest(testName):
            return self.BASE_PRIORITY_TARGETED_COMMIT - commitLevel / 10000.0 - \
                commit.totalNonTimedOutRuns(testName) * weightPerNonTimedOutRun

        return 0 - commitLevel / 10000.0 - commit.totalNonTimedOutRuns(testName) / 10.0

    def updateBranchesUnderTest(self, lock=None):
        self.updateBranchList(lock)

        for branch in self.branches.values():
            branch.updateCommitsUnderTest(self, lock)

    def updateBranchList(self, lock=None):
        if lock:
            lock.release()

        t0 = time.time()
        branchNames = set(self.github.listBranches())
        logging.info("listing github branches took %s seconds", time.time() - t0)

        if lock:
            lock.acquire()

        t0 = time.time()
        for b in branchNames:
            if b not in self.branches:
                self.branches[b] = Branch.Branch(self.testDb,
                                                 b,
                                                 "%s ^%s^" % (b, self.settings.baseline_branch))

        for b in set(self.branches.keys()) - branchNames:
            branch = self.branches[b]
            for c in branch.commits.itervalues():
                c.branches.discard(branch)

            del self.branches[b]

        if self.settings.baseline_branch != 'origin/master' and self.settings.baseline_depth == 0:
            bottom_commit = "%s ^origin/master" % (self.settings.baseline_branch,)
        else:
            bottom_commit = "{baseline} ^{baseline}{carrets}".format(
                baseline=self.settings.baseline_branch,
                carrets='^'*self.settings.baseline_depth
                )

        if self.settings.baseline_branch not in self.branches:
            self.branches[self.settings.baseline_branch] = Branch.Branch(
                self.testDb,
                self.settings.baseline_branch,
                bottom_commit
                )
        else:
            self.branches[self.settings.baseline_branch].updateRevList(
                bottom_commit,
                self
                )
        logging.info("creating new branches took %s seconds", time.time() - t0)

        t0 = time.time()
        self.pruneUnusedCommits()
        logging.info("pruning unused commits took %s seconds", time.time() - t0)

    def pruneUnusedCommits(self):
        toPrune = set()
        for c in self.commits.values():
            if not c.branches:
                toPrune.add(c)

        for c in toPrune:
            del self.commits[c.commitId]

    def loadTestResults(self, commits):
        for commitId, commit in commits.iteritems():
            for testId in self.testDb.getTestIdsForCommit(commitId):
                testData = self.testDb.loadTestResultForTestId(testId)
                if testData:
                    commit.addTestResult(testData, updateDB=False)

    def createCommit(self, commitId, parentHash, commitTitle):
        if commitId not in self.commits:
            testScriptDefinitions = self.testDb.getTestScriptDefinitionsForCommit(commitId)

            if testScriptDefinitions is None:
                testScriptDefinitions = self.github.getTestScriptDefinitionsForCommit(commitId)
                self.testDb.setTestScriptDefinitionsForCommit(commitId, testScriptDefinitions)

            self.commits[commitId] = Commit.Commit(self.testDb,
                                                   commitId,
                                                   parentHash,
                                                   commitTitle,
                                                   testScriptDefinitions)

        return self.commits[commitId]

