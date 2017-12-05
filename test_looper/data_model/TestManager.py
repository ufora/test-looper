import collections
import logging
import random
import time
import traceback
import simplejson
import threading
import test_looper.core.object_database as object_database
import test_looper.core.algebraic as algebraic

import test_looper.data_model.TestResult as TestResult
import test_looper.data_model.Types as Types

import test_looper.data_model.BlockingMachines as BlockingMachines
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.data_model.Branch as Branch
import test_looper.data_model.Commit as Commit
from test_looper.data_model.CommitAndTestToRun import CommitAndTestToRun

pending = Types.BackgroundTaskStatus.Pending()
running = Types.BackgroundTaskStatus.Running()

TestManagerSettings = algebraic.Alternative("TestManagerSettings")
TestManagerSettings.Settings = {
    "max_test_count": int
    }

class TestManager(object):
    def __init__(self, source_control, kv_store, settings):
        self.source_control = source_control

        self.database = object_database.Database(kv_store)
        Types.setup_types(self.database)

        self.settings = settings

    def recordMachineHeartbeat(self, machineId, curTimestamp):
        with self.database.transaction() as t:
            machine = t.indexLookupAny(self.database.Machine, machineId=machineId)

            if machine is None:
                machine = self.database.Machine.New(machineId=machineId)
                machine.lastHeartbeat=curTimestamp
                machine.firstSeen=curTimestamp
                return True
            else:
                machine.lastHeartbeat=curTimestamp
                return False

    def markRepoListDirty(self, curTimestamp):
        self.createTask(self.database.BackgroundTask.RefreshRepos())

    def markBranchListDirty(self, reponame, curTimestamp):
        self.createTask(self.database.BackgroundTask.RefreshBranches(repo=reponame))

    def recordTestResults(self, success, testId, curTimestamp):
        with self.database.transaction() as t:
            runningTest = self.database.RunningTest(testId)
            
            finished_test = self.database.CompletedTest.New(
                test=runningTest.test,
                startedTimestamp=runningTest.startedTimestamp,
                endTimestamp=curTimestamp,
                machine=runningTest.machine,
                success=success
                )
            finished_test.test.activeRuns = finished_test.test.activeRuns - 1
            finished_test.test.totalRuns = finished_test.test.totalRuns + 1

            if success:
                finished_test.test.successes = finished_test.test.successes + 1

            runningTest.delete()

            for dep in t.indexLookup(self.database.TestDependency, dependsOn=finished_test.test):
                self._updateTestPriority(dep.test)

    def testHeartbeat(self, testId, timestamp):
        with self.database.transaction() as t:
            test = self.database.RunningTest(testId)
            assert test.test is not self.database.Test.Null

            test.lastHeartbeat = timestamp

    def startNewTest(self, machineId, timestamp):
        """Allocates a new test and returns (commitId, testName, testId) or (None,None,None) if no work."""
        self.recordMachineHeartbeat(machineId, timestamp)

        with self.database.transaction() as t:
            test = (t.indexLookupAny(self.database.Test, priority=self.database.TestPriority.FirstBuild())
                 or t.indexLookupAny(self.database.Test, priority=self.database.TestPriority.FirstTest())
                 or t.indexLookupAny(self.database.Test, priority=self.database.TestPriority.WantsMoreTests())
                 )

            if not test:
                return None, None, None

            for p in [
                self.database.TestPriority.FirstBuild(),
                self.database.TestPriority.FirstTest(),
                self.database.TestPriority.WantsMoreTests(),
                self.database.TestPriority.WaitingOnBuilds(),
                self.database.TestPriority.UnresolvedDependencies()
                ]:
                if p == test.priority:
                    assert test in t.indexLookup(self.database.Test, priority=p)
                else:
                    assert test not in t.indexLookup(self.database.Test, priority=p), (test.priority, p)

            test.activeRuns = test.activeRuns + 1

            runningTest = self.database.RunningTest.New(
                test=test,
                startedTimestamp=timestamp,
                lastHeartbeat=timestamp,
                machine=t.indexLookupOne(self.database.Machine, machineId=machineId)
                )

            self._updateTestPriority(test)

            commitId = test.commitData.commit.repo.name + "/" + test.commitData.commit.hash

            return (commitId, test.fullname, runningTest._identity)

    def createTask(self, task):
        with self.database.transaction():
            self.database.DataTask.New(task=task, status=pending)

    def performBackgroundWork(self, curTimestamp):
        with self.database.transaction() as v:
            task = v.indexLookupAny(
                self.database.DataTask, 
                status=pending
                )
            if task is None:
                return None

            task.status = running

            testDef = task.task

        try:
            self._processTask(testDef, curTimestamp)
        except:
            traceback.print_exc()
            logging.error("Exception processing task %s:\n\n%s", testDef, traceback.format_exc())
        finally:
            with self.database.transaction():
                task.delete()

        return testDef

    def _processTask(self, task, curTimestamp):
        if task.matches.RefreshRepos:
            all_repos = set(self.source_control.listRepos())

            with self.database.transaction() as t:
                repos = t.indexLookup(self.database.Repo, isActive=True)

                for r in repos:
                    if r.name not in all_repos:
                        r.delete()

                existing = set([x.name for x in repos])

                for new_repo_name in all_repos - existing:
                    r = self.database.Repo.New(name=new_repo_name,isActive=True)
                    self.database.DataTask.New(
                        task=self.database.BackgroundTask.RefreshBranches(r),
                        status=pending
                        )

        elif task.matches.RefreshBranches:
            with self.database.transaction() as t:
                repo = self.source_control.getRepo(task.repo.name)

                branchnames = repo.listBranches()

                branchnames_set = set(branchnames)

                db_repo = task.repo

                db_branches = t.indexLookup(self.database.Branch, repo=db_repo)

                final_branches = tuple([x for x in db_branches if x.name in branchnames_set])
                for branch in db_branches:
                    if branch.name not in branchnames_set:
                        branch.delete()

                for newname in branchnames_set - set([x.name for x in db_branches]):
                    newbranch = self.database.Branch.New(branchname=newname, repo=db_repo)

                    self.database.DataTask.New(
                        task=self.database.BackgroundTask.UpdateBranchTopCommit(newbranch),
                        status=pending
                        )

        elif task.matches.UpdateBranchTopCommit:
            with self.database.transaction() as t:
                repo = self.source_control.getRepo(task.branch.repo.name)
                commit = repo.branchTopCommit(task.branch.branchname)

                if commit:
                    task.branch.head = self._lookupCommitByHash(task.branch.repo, commit)

        elif task.matches.UpdateCommitData:
            with self.database.transaction() as t:
                repo = self.source_control.getRepo(task.commit.repo.name)
                
                commit = task.commit

                if commit.data is self.database.CommitData.Null:
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

                    if "[nft]" not in subject:
                        try:
                            defText = repo.getTestScriptDefinitionsForCommit(task.commit.hash)
                            all_tests = TestDefinitionScript.extract_tests_from_str(defText)
                            
                            for e in all_tests.values():
                                fullname=commit.repo.name + "/" + commit.hash + "/" + e.name

                                self.createTest_(
                                    commitData=commit.data,
                                    fullname=fullname,
                                    testDefinition=e
                                    )
                        except Exception as e:
                            traceback.print_exc()

                            logging.warn("Got an error parsing tests for %s:\n%s", commit.hash, traceback.format_exc())

                            commit.data.testDefinitionsError=str(e)

        elif task.matches.UpdateTestPriority:
            with self.database.transaction() as t:
                self._updateTestPriority(task.test)
        else:
            raise Exception("Unknown task: %s" % task)

    def _updateTestPriority(self, test):
        t = self.database.current_transaction()

        if t.indexLookup(self.database.UnresolvedTestDependency, test=test):
            test.priority = self.database.TestPriority.UnresolvedDependencies()
        elif self._testHasUnfinishedDeps(test):
            test.priority = self.database.TestPriority.WaitingOnBuilds()
        elif self._testHasFailedDeps(test):
            test.priority = self.database.TestPriority.DependencyFailed()
        elif self._testNeedsNoMoreBuilds(test):
            test.priority = self.database.TestPriority.NoMoreTests()
        elif test.testDefinition.matches.Build:
            test.priority = self.database.TestPriority.FirstBuild()
        elif test.totalRuns == 0:
            test.priority = self.database.TestPriority.FirstTest()
        else:
            test.priority = self.database.TestPriority.WantsMoreTests()

    def _testNeedsNoMoreBuilds(self, test):
        if test.testDefinition.matches.Deployment:
            needed = 0
        elif test.testDefinition.matches.Build:
            needed = 1
        else:
            needed = max(test.runsDesired, self.settings.max_test_count)

        return test.totalRuns + test.activeRuns >= needed

    def _testHasUnfinishedDeps(self, test):
        deps = self.database.current_transaction().indexLookup(self.database.TestDependency, test=test)
        unresolved_deps = self.database.current_transaction().indexLookup(self.database.UnresolvedTestDependency, test=test)

        for dep in deps:
            if dep.dependsOn.totalRuns == 0:
                return True
        return False

    def _testHasFailedDeps(self, test):
        for dep in self.database.current_transaction().indexLookup(self.database.TestDependency, test=test):
            if dep.dependsOn.successes == 0:
                return True
        return False

    def createTest_(self, commitData, fullname, testDefinition):
        #make sure it's new
        assert not self.database.current_transaction().indexLookup(self.database.Test, fullname=fullname)
        
        test = self.database.Test.New(
            commitData=commitData, 
            fullname=fullname, 
            testDefinition=testDefinition, 
            priority=self.database.TestPriority.UnresolvedDependencies()
            )

        #now first check whether this test has any unresolved dependencies
        for depname, dep in testDefinition.dependencies.iteritems():
            if dep.matches.ExternalBuild:
                fullname_dep = "/".join([dep.repo, dep.commitHash, dep.name, dep.environment])
                self.createTestDep_(test, fullname_dep)
            elif dep.matches.InternalBuild:
                fullname_dep = "/".join([commitData.commit.repo.name, commitData.commit.hash, dep.name, dep.environment])
                self.createTestDep_(test, fullname_dep)
            elif dep.matches.Source:
                fullname_dep = "/".join([dep.repo, dep.commitHash, "source"])
                self.createTestDep_(test, fullname_dep)

        env = testDefinition.environment
        if env.matches.Import:
            self._lookupCommitByHash(env.repo, env.commitHash)
            self.createTestDep_(test, "/".join([env.repo, env.commitHash, "source"]))

        self._markTestFullnameCreated(fullname, test)
        self._triggerTestPriorityUpdateIfNecessary(test)

    def createTestDep_(self, test, fullname_dep):
        dep_test = self.database.current_transaction().indexLookupAny(self.database.Test, fullname=fullname_dep)

        if not dep_test:
            self.database.UnresolvedTestDependency.New(test=test, dependsOnName=fullname_dep)
        else:
            self.database.TestDependency.New(test=test, dependsOn=dep_test)

    def _markTestFullnameCreated(self, fullname, test_for_name=None):
        for dep in self.database.current_transaction().indexLookup(self.database.UnresolvedTestDependency, dependsOnName=fullname):
            test = dep.test
            if test_for_name is not None:
                #this is a real test. If we depended on a commit, this would have been
                #None
                self.database.TestDependency.New(test=test, dependsOn=test_for_name)

            dep.delete()
            self._triggerTestPriorityUpdateIfNecessary(test)
                
    def _triggerTestPriorityUpdateIfNecessary(self, test):
        if not self.database.current_transaction().indexLookup(self.database.UnresolvedTestDependency, test=test):
            self.database.DataTask.New(
                task=self.database.BackgroundTask.UpdateTestPriority(test=test),
                status=pending
                )

    def _lookupCommitByHash(self, repo, commitHash):
        if isinstance(repo, str):
            repo = self.database.current_transaction().indexLookupAny(self.database.Repo, name=repo)
            if not repo:
                logging.warn("Unknown repo %s", repo)
            return repo

        commit = self.database.current_transaction().indexLookupAny(self.database.Commit, repo_and_hash=(repo, commitHash))

        if not commit:
            commit = self.database.Commit.New(repo=repo, hash=commitHash)
            self.database.DataTask.New(
                task=self.database.BackgroundTask.UpdateCommitData(commit=commit),
                status=pending
                )
            self._markTestFullnameCreated("/".join([repo.name, commitHash, "source"]))

        return commit






class Old:
    def initialize(self):
        self.updateBranchesUnderTest()
        self.loadTestResults(self.commits)

    def getCommitByCommitId(self, commitId):
        if not commitId in self.commits:
            repoName, commitHash = commitId.split("/")

            repo = self.source_control.getRepo(repoName)

            _, parentHashes, commitTitle = repo.hashParentsAndCommitTitleFor(commitHash)

            self.commits[commitId] = self.createCommit(repoName + "/" + commitHash, parentHashes, commitTitle)

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

    def distinctRepoNames(self):
        return set([x.split("/")[0] for x in self.branches.keys()])

    def branchesForRepo(self, repoName):
        return set([x for x in self.branches.keys() if x.split("/")[0] == repoName])

    def distinctBranches(self):
        return set(self.branches.keys())

    def commitsInBranch(self, branchName):
        return self.branches[branchName].commits.values()

    def getPossibleCommitsAndTests(self, workerInfo):
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

        parentIds = set(c.parentId for c in self.commits.itervalues() if c.parentId is not None)
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

    def scoreCommitAndTest(self, commitLevelDict, commit, testName):
        """Returns the priority score for this commit"""
        BASE_PRIORITY_UNKNOWN_COMMIT       = 10000000000000
        BASE_PRIORITY_UNBUILT_COMMIT       = 1000000000
        BASE_PRIORITY_PERIODIC_TEST_COMMIT = 1000000
        BASE_PRIORITY_UNTESTED_COMMIT      = 100000
        BASE_PRIORITY_TARGETED_COMMIT      = 1000

        commitLevel = commitLevelDict[commit.commitId]

        if testName is None:
            return BASE_PRIORITY_UNKNOWN_COMMIT - commitLevel
        if testName == "build":
            return BASE_PRIORITY_UNBUILT_COMMIT - commitLevel
        if commit.isPeriodicTest(testName):
            return BASE_PRIORITY_PERIODIC_TEST_COMMIT - commitLevel
        if commit.totalNonTimedOutRuns(testName) == 0:
            return BASE_PRIORITY_UNTESTED_COMMIT - commitLevel / 10000.0
        if commit.isTargetedTest(testName):
            return BASE_PRIORITY_TARGETED_COMMIT - commitLevel / 10000.0 - \
                commit.totalNonTimedOutRuns(testName)

        return 0 - commitLevel / 10000.0 - commit.totalNonTimedOutRuns(testName) / 10.0

    def updateBranchesUnderTest(self):
        self.updateBranchList()

        for branch in self.branches.values():
            branch.updateCommitsUnderTest(self)

    def updateBranchList(self):
        t0 = time.time()

        self.source_control.refresh()

        branchNames = set(self.source_control.listBranches())
        logging.info("listing branches took %.2f seconds", time.time() - t0)

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
            repoName, commitHash = commitId.split("/")

            repo = self.source_control.getRepo(repoName)
            data = repo.getTestScriptDefinitionsForCommit(commitHash)

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

