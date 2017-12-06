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

    def commitsToDisplayForBranch(self, branch):
        commits = set()
        new = [branch.head]
        while new:
            n = new.pop()
            if n not in commits:
                commits.add(n)
                if n.data is not self.database.Commit.Null:
                    for child in n.data.parents:
                        new.append(child)
        return commits

    def toggleBranchUnderTest(self, branch):
        branch.isUnderTest = not branch.isUnderTest
        self._triggerCommitPriorityUpdate(branch.head)


    def getTestById(self, testIdentity):
        t = self.database.RunningTest(testIdentity)
        if t.exists():
            return t
        t = self.database.CompletedTest(testIdentity)
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
        with self.database.transaction() as t:
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

            for dep in self.database.TestDependency.lookupAll(dependsOn=finished_test.test):
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
            test = (self.database.Test.lookupAny(priority=self.database.TestPriority.FirstBuild())
                 or self.database.Test.lookupAny(priority=self.database.TestPriority.FirstTest())
                 or self.database.Test.lookupAny(priority=self.database.TestPriority.WantsMoreTests())
                 )

            if not test:
                return None, None, None

            test.activeRuns = test.activeRuns + 1

            runningTest = self.database.RunningTest.New(
                test=test,
                startedTimestamp=timestamp,
                lastHeartbeat=timestamp,
                machine=self.database.Machine.lookupOne(machineId=machineId)
                )

            self._updateTestPriority(test)

            commitId = test.commitData.commit.repo.name + "/" + test.commitData.commit.hash

            return (commitId, test.fullname, runningTest._identity)

    def createTask(self, task):
        with self.database.transaction():
            self.database.DataTask.New(task=task, status=pending)

    def performBackgroundWork(self, curTimestamp):
        with self.database.transaction() as v:
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
            with self.database.transaction():
                task.delete()

        return testDef

    def _processTask(self, task, curTimestamp):
        if task.matches.RefreshRepos:
            all_repos = set(self.source_control.listRepos())

            with self.database.transaction() as t:
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
            with self.database.transaction() as t:
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
        elif task.matches.UpdateCommitPriority:
            assert False, "Not implemented yet"
        else:
            raise Exception("Unknown task: %s" % task)

    def _updateTestPriority(self, test):
        t = self.database.current_transaction()

        if self.database.UnresolvedTestDependency.lookupAll(test=test):
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
                self._createSourceDep(test, dep.repo.name, dep.commitHash)

        env = testDefinition.environment
        if env.matches.Import:
            self._lookupCommitByHash(env.repo, env.commitHash)
            self._createSourceDep(test, env.repo, env.commitHash)

        self._markTestFullnameCreated(fullname, test)
        self._triggerTestPriorityUpdateIfNecessary(test)

    def _createSourceDep(self, test, reponame, commitHash):
        t = self.database.current_transaction()
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
