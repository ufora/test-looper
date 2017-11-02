import collections
import logging
import random
import time
import traceback
import simplejson
import test_looper.core.TestResult as TestResult

import test_looper.server.BlockingMachines as BlockingMachines
import test_looper.core.TestScriptDefinition as TestScriptDefinition
import test_looper.server.Branch as Branch
import test_looper.server.Commit as Commit
from test_looper.server.CommitAndTestToRun import CommitAndTestToRun


class TestManagerSettings:
    def __init__(self, baseline_branch, baseline_depth, max_test_count, test_definitions_default):
        self.baseline_branch = baseline_branch
        self.baseline_depth = baseline_depth
        self.max_test_count = max_test_count
        self.test_definitions_default = test_definitions_default


class TestManager(object):
    def __init__(self, src_ctrl, test_db, lock, settings):
        self.src_ctrl = src_ctrl
        self.testDb = test_db
        self.settings = settings

        self.mostRecentTouchByMachine = {}
        self.branches = {}
        self.commits = {}
        self.periodicTestRunBranchCandidates = ['origin/master']

        #dict from internalIpAddress to properties of blocking machines
        self.blockingMachines = BlockingMachines.BlockingMachines()
        self.lock = lock

    def clearResultsForTestIdCommitId(self, testId, commitId):
        self.testDb.clearResultsForTestIdCommitId(testId, commitId)
        commit = self.commits[commitId]
        test = commit.testsById[testId]
        commit.clearTestResult(test.testName, testId)

    def recordMachineObservation(self, machineId):
        new_machine = machineId not in self.mostRecentTouchByMachine
        self.mostRecentTouchByMachine[machineId] = time.time()
        return new_machine

    def refresh(self, lock=None):
        self.updateBranchesUnderTest(lock)

    def initialize(self):
        self.updateBranchesUnderTest()
        self.loadTestResults(self.commits)

    def getCommitByCommitId(self, commitId):
        if not commitId in self.commits:
            commitId, parentHashes, commitTitle = self.src_ctrl.commitsBetweenCommitIds(commitId, commitId + "^^")[0]
            self.commits[commitId] = self.createCommit(commitId, parentHashes, commitTitle)
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
                            time.time() - lastTestRunStarted > \
                                (periodicTest.periodicTestPeriodInHours * 60 * 60):
                        result.append((mostRecentCommit, periodicTest.testName))

        return result


    def getPossibleCommitsAndTests(self, workerInfo=None):
        """Return a list consisting of all possible commit/test combinations we'd consider running.

        Each item the list is a tuple

            (commit, test)

        where commit is a Commit object and 'test' is either a string giving the test name or None
        indicating that we don't know the list of commits.
        """
        result = []
        for commit in self.commits.itervalues():
            if (commit.excludeFromTestingBecauseOfCommitSubject() or
                    commit.buildInProgress() or commit.isBrokenBuild() or
                    not commit.isUnderTest):
                continue

            if commit.needsBuild():
                testDef = commit.getTestDefinitionFor('build')
                if (testDef is not None and (
                        workerInfo is None or
                        self.blockingMachines.machineCanParticipateInTest(workerInfo,
                                                                          testDef))):
                    result.append((commit, 'build'))
            else:
                result += [
                    (commit, testName) for testName in commit.statsByType.iterkeys()
                    if testName != 'build' and self.should_test(commit, testName, workerInfo)
                    ]

        return result


    def should_test(self, commit, testName, workerInfo):
        test_def = commit.getTestDefinitionFor(testName)
        if test_def is None:
            return False

        under_max_test_count = (
            commit.isTargetedTest(testName) or
            commit.statsByType[testName].completedCount < self.settings.max_test_count
            )
        worker_can_participate = (
            workerInfo is None or
            self.blockingMachines.machineCanParticipateInTest(workerInfo, test_def)
            )
        return not test_def.periodicTest and under_max_test_count and worker_can_participate


    def getTask(self, workerInfo):
        t0 = time.time()
        allCommitsToTest = self.getPossibleCommitsAndTests(workerInfo)
        possible_commits_time = time.time()
        candidates = self.prioritizeCommitsAndTests(allCommitsToTest)
        prioritization_time = time.time()

        if not candidates:
            return None, None, None


        firstCandidate = candidates[0]
        commit = firstCandidate.commit
        testName = firstCandidate.testName

        testDefinition = commit.getTestDefinitionFor(testName)
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
        test_assignment_time = time.time()

        if testResult is None:
            return None, None, None

        if testResult.commitId != commit.commitId:
            commit = self.commits[testResult.commitId]

        if testResult.testId not in commit.testsById:
            commit.addTestResult(testResult, updateDB=True)
            self.testDb.updateTestResult(testResult)

        end_time = time.time()

        logging.info("getTask timing - Total: %.2f, possible_commits: %.2f, "
                     "prioritization: %.2f, assignment: %.2f, add_result: %.2f",
                     end_time - t0,
                     possible_commits_time - t0,
                     prioritization_time - possible_commits_time,
                     test_assignment_time - prioritization_time,
                     end_time - test_assignment_time)

        return commit, commit.getTestDefinitionFor(testResult.testName), testResult


    def heartbeat(self, testId, commitId, machineId):
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

        test.recordMachineResult(result)
        self.commits[commitId].testChanged(test.testName, test)
        self.testDb.updateTestResult(test)

    def computeCommitLevels(self):
        """Given a set of Commit objects, produce a dictionary from commitId to "level",
        where 'level' is 0 for leaf commits and increases by 1 at each parent."""
        commitLevel = {}

        parentIds = set(c.parentId for c in self.commits.itervalues())
        leaves = set(commit for commit_id, commit in self.commits.iteritems()
                     if commit_id not in parentIds)

        def followChain(commit, level):
            if commit.commitId not in commitLevel or commitLevel[commit.commitId] > level:
                commitLevel[commit.commitId] = level

                for parent_commit in (self.commits.get(parent_id)
                                      for parent_id in commit.parentIds):
                    if parent_commit:
                        followChain(parent_commit, level+1)

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
            targetedCandidates = [
                (commit, test) for commit, test in candidates
                if commit.isTargetedTest(test)
                ]
            if len(targetedCandidates) > 0 and random.random() < 0.5:
                candidates = targetedCandidates

        commitLevelDict = self.computeCommitLevels()

        def scoreCommitAndTest(candidate):
            return self.scoreCommitAndTest(commitLevelDict, candidate[0], candidate[1])


        commitsAndTestsToRun = [CommitAndTestToRun(candidate[1],
                                                   candidate[0],
                                                   scoreCommitAndTest(candidate))
                                for candidate in candidates]

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
        if commit.isTargetedTest(testName):
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
        branchNames = set(self.src_ctrl.listBranches())
        logging.info("listing branches took %.2f seconds", time.time() - t0)

        if lock:
            lock.acquire()

        t0 = time.time()

        logging.info(
            "Comparing new branchlist of %s to existing branchlist of %s with baseline branch of %s", 
            sorted(branchNames),
            sorted(self.branches),
            self.settings.baseline_branch
            )

        for b in branchNames:
            if b not in self.branches:
                logging.info("Create a new branch %s", b)

                self.branches[b] = Branch.Branch(self.testDb,
                                                 b,
                                                 self.settings.baseline_branch
                                                 )

        for b in set(self.branches.keys()) - branchNames:
            logging.info("Removing branch %s", b)
            
            branch = self.branches[b]
            for c in branch.commits.itervalues():
                c.branches.discard(branch)

            del self.branches[b]

        if self.settings.baseline_branch != 'origin/master' and self.settings.baseline_depth == 0:
            bottom_commit = "origin/master"
        else:
            bottom_commit = "{baseline}{carrets}".format(
                baseline=self.settings.baseline_branch,
                carrets='^'*self.settings.baseline_depth
                )

        if self.settings.baseline_branch not in self.branches:
            logging.info("Recreating baseline branch %s", self.settings.baseline_branch)

            self.branches[self.settings.baseline_branch] = Branch.Branch(
                self.testDb,
                self.settings.baseline_branch,
                bottom_commit
                )
        else:
            logging.info("Resetting baseline branch %s bottom commit", self.settings.baseline_branch)

            self.branches[self.settings.baseline_branch].updateRevList(
                bottom_commit,
                self
                )
        logging.info("creating new branches took %.2f seconds", time.time() - t0)

        t0 = time.time()
        self.pruneUnusedCommits()
        logging.info("pruning unused commits took %.2f seconds", time.time() - t0)

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

    def testDefinitionsForCommit(self, commitId):
        json = self.testDb.getTestScriptDefinitionsForCommit(commitId)

        if json is None:
            data = self.src_ctrl.getTestScriptDefinitionsForCommit(commitId)

            if data is None:
                json = {}
            else:
                try:
                    json = simplejson.loads(data)
                except:
                    logging.error("Contents of test definitions for %s are not valid json.\n%s" % 
                        (commitId, traceback.format_exc()))

                    json = {}

            self.testDb.setTestScriptDefinitionsForCommit(commitId, json)

        return TestScriptDefinition.TestDefinitions.fromJson(json)

    def createCommit(self, commitId, parentHashes, commitTitle):
        if commitId not in self.commits:
            try:
                testScriptDefinitions = self.testDefinitionsForCommit(commitId).getTestsAndBuild()
                testScriptDefinitionsError = None
            except Exception as e:
                testScriptDefinitions = None
                testScriptDefinitionsError = e.message

            self.commits[commitId] = Commit.Commit(self.testDb,
                                                   commitId,
                                                   parentHashes,
                                                   commitTitle,
                                                   testScriptDefinitions or [],
                                                   testScriptDefinitionsError
                                                   )

        return self.commits[commitId]

