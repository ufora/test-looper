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

MAX_TEST_PRIORITY=100

class TestManager(object):
    def __init__(self, source_control, kv_store, settings):
        self.source_control = source_control

        self.database = object_database.Database(kv_store)
        Types.setup_types(self.database)

        self.settings = settings

        self.writelock = threading.Lock()

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

    def commitsToDisplayForBranch(self, branch):
        commits = set()
        ordered = []
        new = [branch.head]
        while new:
            n = new.pop()
            if n not in commits:
                commits.add(n)
                ordered.append(n)

                if n.data:
                    for child in n.data.parents:
                        new.append(child)

        return ordered

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

    def recordMachineHeartbeat(self, machineId, curTimestamp):
        with self.transaction_and_lock():
            machine = self.database.Machine.lookupAny(machineId=machineId)

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
        with self.transaction_and_lock():
            testRun = self.database.TestRun(str(testId))

            assert testRun.exists(), "Can't find %s" % testId
            assert not testRun.canceled, "test is already canceled"

            testRun.endTimestamp = curTimestamp
            
            testRun.test.activeRuns = testRun.test.activeRuns - 1
            testRun.test.totalRuns = testRun.test.totalRuns + 1

            testRun.success = success

            if success:
                testRun.test.successes = testRun.test.successes + 1

            for dep in self.database.TestDependency.lookupAll(dependsOn=testRun.test):
                self._updateTestPriority(dep.test)

    def testHeartbeat(self, testId, timestamp):
        logging.info('test %s heartbeating', testId)
        with self.transaction_and_lock():
            test = self.database.TestRun(str(testId))

            if not test.exists():
                return False

            if test.canceled:
                return False

            test.lastHeartbeat = timestamp

            return True

    def _lookupHighestPriorityTest(self):
        for priorityType in [
                self.database.TestPriority.FirstBuild,
                self.database.TestPriority.FirstTest,
                self.database.TestPriority.WantsMoreTests
                ]:
            for priority in reversed(range(1,MAX_TEST_PRIORITY+1)):
                test = self.database.Test.lookupAny(priority=priorityType(priority))
                if test:
                    logging.info("Returning test %s of priority %s", test.fullname, priorityType(priority))
                    return test

    def startNewTest(self, machineId, timestamp):
        """Allocates a new test and returns (commitId, testName, testId) or (None,None,None) if no work."""
        self.recordMachineHeartbeat(machineId, timestamp)

        with self.transaction_and_lock():
            test = self._lookupHighestPriorityTest()

            if not test:
                return None, None, None

            test.activeRuns = test.activeRuns + 1

            runningTest = self.database.TestRun.New(
                test=test,
                startedTimestamp=timestamp,
                lastHeartbeat=timestamp,
                machine=self.database.Machine.lookupOne(machineId=machineId)
                )

            self._updateTestPriority(test)

            commitId = test.commitData.commit.repo.name + "/" + test.commitData.commit.hash

            return (commitId, test.testDefinition.name, runningTest._identity)

    def createTask(self, task):
        with self.transaction_and_lock():
            self.database.DataTask.New(task=task, status=pending)

    def performBackgroundWork(self, curTimestamp):
        with self.transaction_and_lock():
            task = self.database.DataTask.lookupAny(status=pending)
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
            with self.transaction_and_lock():
                task.delete()

        return testDef

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
                    self.database.DataTask.New(
                        task=self.database.BackgroundTask.RefreshBranches(r),
                        status=pending
                        )

        elif task.matches.RefreshBranches:
            with self.transaction_and_lock():
                repo = self.source_control.getRepo(task.repo.name)

                branchnames = repo.listBranches()

                branchnames_set = set(branchnames)

                db_repo = task.repo

                db_branches = self.database.Branch.lookupAll(repo=db_repo)

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
            with self.transaction_and_lock():
                repo = self.source_control.getRepo(task.branch.repo.name)
                commit = repo.branchTopCommit(task.branch.branchname)

                if commit:
                    task.branch.head = self._lookupCommitByHash(task.branch.repo, commit)

        elif task.matches.UpdateCommitData:
            with self.transaction_and_lock():
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
                    for p in parents:
                        self.database.CommitRelationship.New(child=commit,parent=p)

                    self._triggerCommitPriorityUpdate(commit)

                    if "[nft]" not in subject:
                        try:
                            commitId = commit.repo.name + "/" + commit.hash

                            defText, extension = repo.getTestScriptDefinitionsForCommit(task.commit.hash)
                            
                            all_tests, all_environments = TestDefinitionScript.extract_tests_from_str(commitId, extension, defText)
                            
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
            with self.transaction_and_lock():
                self._updateTestPriority(task.test)
        elif task.matches.UpdateCommitPriority:
            with self.transaction_and_lock():
                self._updateCommitPriority(task.commit)
        else:
            raise Exception("Unknown task: %s" % task)

    def _updateCommitPriority(self, commit):
        priority = self._computeCommitPriority(commit)
        
        logging.info("Commit %s/%s has new priority %s%s%s", 
            commit.repo.name, 
            commit.hash, 
            priority,
            "==" if priority == commit.priority else "!=",
            commit.priority
            )

        if priority != commit.priority:
            commit.priority = priority

            if commit.data:
                if commit.priority == 0:
                    for test in self.database.Test.lookupAll(commitData=commit.data):
                        for run in self.database.TestRun.lookupAll(test=test):
                            if run.endTimestamp == 0.0:
                                run.canceled = True
                                test.activeRuns = test.activeRuns - 1

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

        for test in self.database.Test.lookupAll(commitData=commit.data):
            for dep in self.database.TestDependency.lookupAll(dependsOn=test):
                needing_us = dep.test
                if commit != needing_us.commitData.commit:
                    priority = max(needing_us.commitData.commit.priority, priority)

        return priority

    def _updateTestPriority(self, test):
        if self.database.UnresolvedTestDependency.lookupAll(test=test):
            test.priority = self.database.TestPriority.UnresolvedDependencies()
        elif self._testHasUnfinishedDeps(test):
            test.priority = self.database.TestPriority.WaitingOnBuilds()
        elif self._testHasFailedDeps(test):
            test.priority = self.database.TestPriority.DependencyFailed()
        elif self._testNeedsNoMoreBuilds(test):
            test.priority = self.database.TestPriority.NoMoreTests()
        elif test.testDefinition.matches.Build:
            test.priority = self.database.TestPriority.FirstBuild(priority=test.commitData.commit.priority)
        elif (test.totalRuns + test.activeRuns) == 0:
            test.priority = self.database.TestPriority.FirstTest(priority=test.commitData.commit.priority)
        else:
            test.priority = self.database.TestPriority.WantsMoreTests(priority=test.commitData.commit.priority)

    def _testNeedsNoMoreBuilds(self, test):
        if test.commitData.commit.priority == 0:
            return True

        if test.testDefinition.matches.Deployment:
            needed = 0
        elif test.testDefinition.matches.Build:
            needed = 1
        else:
            needed = max(test.runsDesired, self.settings.max_test_count)

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

    def createTest_(self, commitData, fullname, testDefinition):
        #make sure it's new
        assert not self.database.Test.lookupAll(fullname=fullname)
        
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
                self._createTestDep(test, fullname_dep)
            elif dep.matches.InternalBuild:
                fullname_dep = "/".join([commitData.commit.repo.name, commitData.commit.hash, dep.name, dep.environment])
                self._createTestDep(test, fullname_dep)
            elif dep.matches.Source:
                self._createSourceDep(test, dep.repo, dep.commitHash)

        env = testDefinition.environment
        if env.matches.Import:
            self._lookupCommitByHash(env.repo, env.commitHash)
            self._createSourceDep(test, env.repo, env.commitHash)

        self._markTestFullnameCreated(fullname, test)
        self._triggerTestPriorityUpdateIfNecessary(test)

    def _createSourceDep(self, test, reponame, commitHash):
        repo = self.database.Repo.lookupAny(name=reponame)
        if not repo:
            self.database.UnresolvedRepoDependency.New(test=test,reponame=reponame, commitHash=commitHash)
            return

        commit = self.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))
        if not commit:
            self.database.UnresolvedSourceDependency.New(test=test, repo=repo, commitHash=commitHash)

    def _createTestDep(self, test, fullname_dep):
        dep_test = self.database.Test.lookupAny(fullname=fullname_dep)

        if not dep_test:
            self.database.UnresolvedTestDependency.New(test=test, dependsOnName=fullname_dep)
        else:
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
            status=pending
            )

    def _triggerTestPriorityUpdateIfNecessary(self, test):
        if not (self.database.UnresolvedTestDependency.lookupAll(test=test) + 
                self.database.UnresolvedRepoDependency.lookupAll(test=test) +
                self.database.UnresolvedSourceDependency.lookupAll(test=test)
                ):
            self.database.DataTask.New(
                task=self.database.BackgroundTask.UpdateTestPriority(test=test),
                status=pending
                )

    def _createRepo(self, new_repo_name):
        r = self.database.Repo.New(name=new_repo_name,isActive=True)

        for dep in self.database.UnresolvedRepoDependency.lookupAll(reponame=new_repo_name):
            self._createSourceDep(test, new_repo_name, dep.commitHash)
            self._triggerTestPriorityUpdateIfNecessary(dep.test)
            dep.delete()

        return r

    def _lookupCommitByHash(self, repo, commitHash):
        if isinstance(repo, str):
            repo = self.database.Repo.lookupAny(name=repo)
            if not repo:
                logging.warn("Unknown repo %s", repo)
            return repo

        commit = self.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))

        if not commit:
            commit = self.database.Commit.New(repo=repo, hash=commitHash)
            self.database.DataTask.New(
                task=self.database.BackgroundTask.UpdateCommitData(commit=commit),
                status=pending
                )

            for dep in self.database.UnresolvedSourceDependency.lookupAll(repo_and_hash=(repo, commitHash)):
                self._triggerTestPriorityUpdateIfNecessary(dep.test)
                dep.delete()


        return commit
