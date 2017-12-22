import collections
import logging
import random
import time
import traceback
import simplejson
import threading
import test_looper.core.object_database as object_database
import test_looper.core.algebraic as algebraic
import test_looper.core.machine_management.MachineManagement as MachineManagement

import test_looper.data_model.Types as Types

import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.data_model.Branch as Branch
import test_looper.data_model.Commit as Commit

pending = Types.BackgroundTaskStatus.Pending()
running = Types.BackgroundTaskStatus.Running()

MAX_TEST_PRIORITY = 100
TEST_TIMEOUT_SECONDS = 60
IDLE_TIME_BEFORE_SHUTDOWN = 30

class TestManager(object):
    def __init__(self, source_control, machine_management, kv_store):
        self.source_control = source_control
        self.machine_management = machine_management

        self.database = object_database.Database(kv_store)
        Types.setup_types(self.database)

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

            if n and n not in commits:
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

    def _machineHeartbeat(self, machine, curTimestamp):
        if machine.firstHeartbeat == 0.0:
            machine.firstHeartbeat = curTimestamp
        machine.lastHeartbeat=curTimestamp
            
    def triggerPruneDeadWorkerMachines(self, curTimestamp):            
        self.createTask(self.database.BackgroundTask.PruneDeadWorkerMachines())

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

            testRun.machine.lastTestCompleted = curTimestamp

            os = testRun.machine.os

            if os.matches.WindowsOneshot or os.matches.LinuxOneshot:
                #we need to shut down this machine since we used it for only one test
                self._terminateMachine(testRun.machine)

            for dep in self.database.TestDependency.lookupAll(dependsOn=testRun.test):
                self._updateTestPriority(dep.test)

            self._updateTestPriority(testRun.test)

    def testHeartbeat(self, testId, timestamp):
        logging.info('test %s heartbeating', testId)
        with self.transaction_and_lock():
            testRun = self.database.TestRun(str(testId))

            if not testRun.exists():
                return False

            if testRun.canceled:
                return False

            if not testRun.machine.isAlive:
                self._cancelTestRun(testRun)
            else:
                self._machineHeartbeat(testRun.machine, timestamp)

                testRun.lastHeartbeat = timestamp

                return True

    def _lookupHighestPriorityTest(self, machine):
        for priorityType in [
                self.database.TestPriority.FirstBuild,
                self.database.TestPriority.FirstTest,
                self.database.TestPriority.WantsMoreTests
                ]:
            for priority in reversed(range(1,MAX_TEST_PRIORITY+1)):
                for test in self.database.Test.lookupAll(priority=priorityType(priority)):
                    if self._machineCategoryPairForTest(test) == (machine.hardware, machine.os):
                        return test

    def startNewTest(self, machineId, timestamp):
        """Allocates a new test and returns (repoName, commitHash, testName, testId) or (None,None,None) if no work."""
        with self.transaction_and_lock():
            machine = self.database.Machine.lookupAny(machineId=machineId)

            assert machine is not None

            self._machineHeartbeat(machine, timestamp)

            test = self._lookupHighestPriorityTest(machine)

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

            self._updateTestPriority(test)

            return (test.commitData.commit.repo.name, test.commitData.commit.hash, test.testDefinition.name, runningTest._identity)

    def createTask(self, task):
        with self.transaction_and_lock():
            self.database.DataTask.New(task=task, status=pending)

    def performCleanupTasks(self, curTimestamp):
        #check all tests to see if we've exceeded the timeout and the test is dead
        with self.transaction_and_lock():
            for t in self.database.TestRun.lookupAll(isRunning=True):
                if t.lastHeartbeat < curTimestamp - TEST_TIMEOUT_SECONDS:
                    self._cancelTestRun(t)

        with self.transaction_and_lock():
            self._scheduleBootCheck()
            self._shutdownMachinesIfNecessary(curTimestamp)
            
    def _scheduleBootCheck(self):
        if not self.database.DataTask.lookupAny(pending_boot_machine_check=True):
            self.database.DataTask.New(
                task=self.database.BackgroundTask.BootMachineCheck(),
                status=pending
                )

    def _cancelTestRun(self, testRun):
        testRun.canceled = True
        testRun.test.activeRuns = testRun.test.activeRuns - 1
        
        self._triggerTestPriorityUpdate(testRun.test)

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

    def _machineTerminated(self, machineId):
        machine = self.database.Machine.lookupOne(machineId=machineId)

        if not machine.isAlive:
            return
        
        for testRun in list(self.database.TestRun.lookupAll(runningOnMachine=machine)):
            self._cancelTestRun(machine.runningTest)

        machine.isAlive = False

        mc = self._machineCategoryForPair(machine.hardware, machine.os)
        mc.booted = mc.booted - 1

        self._scheduleBootCheck()

    def _processTask(self, task, curTimestamp):
        if task.matches.PruneDeadWorkerMachines:
            with self.transaction_and_lock():
                known_workers = [(x.machineId, x.hardware, x.os) for x in self.database.Machine.lookupAll(isAlive=True)]
                to_kill = self.machine_management.synchronize_workers(known_workers)

                for machineId in to_kill:
                    self._machineTerminated(machineId)

        elif task.matches.RefreshRepos:
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
                        status=pending
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
                            status=pending
                            )
                    except:
                        logging.error("Error scheduling branch commit lookup:\n\n%s", traceback.format_exc())

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

                        logging.warn("Got an error parsing tests for %s: '%s'", commit.hash, str(e))

                        commit.data.testDefinitionsError=str(e)

        elif task.matches.UpdateTestPriority:
            with self.transaction_and_lock():
                self._updateTestPriority(task.test)
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

    def _shutdown(self, category, curTimestamp, onlyIdle):
        for machine in self.database.Machine.lookupAll(hardware_and_os=(category.hardware, category.os)):
            if not self.database.TestRun.lookupAny(runningOnMachine=machine):
                if not onlyIdle or (curTimestamp - machine.lastTestCompleted > IDLE_TIME_BEFORE_SHUTDOWN):
                    self._terminateMachine(machine)
                    return True
        return False

    def _shutdownMachinesIfNecessary(self, curTimestamp):
        def check():
            for cat in self.database.MachineCategory.lookupAll(want_less=True):
                if cat.desired < cat.booted:
                    return self._shutdown(cat, curTimestamp, onlyIdle=True)

        while check():
            pass

    def _terminateMachine(self, machine):
        try:
            self.machine_management.terminate_worker(machine.machineId)
        except:
            logging.error("Failed to terminate worker %s because:\n%s", machine.machineId, traceback.format_exc())

        self._machineTerminated(machine.machineId)

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

    def _updateTestPriority(self, test):
        self._checkAllTestDependencies(test)
        
        #cancel any runs already going if this gets deprioritized
        if test.commitData.commit.priority == 0:
            for run in self.database.TestRun.lookupAll(test=test):
                if run.endTimestamp == 0.0 and not run.canceled:
                    self._cancelTestRun(run)

        oldPriority = test.priority
        oldTargetMachineBoot = test.targetMachineBoot

        category = test.machineCategory

        if category and category.hardwareComboUnbootable:
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
            if self._updateTestTargetMachineCountAndReturnIsDone(test):
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
                os = MachineManagement.OsConfig.LinuxOneshot(env.image.base_ami)
            else:
                return None

        if env.platform.matches.windows:
            if env.image.matches.Dockerfile or env.image.matches.DockerfileInline:
                os = MachineManagement.OsConfig.WindowsWithDocker()
            elif env.image.matches.AMI:
                os = MachineManagement.OsConfig.WindowsOneshot(env.image.base_ami)
            else:
                return None

        min_cores = test.testDefinition.min_cores
        max_cores = test.testDefinition.max_cores
        min_ram_gb = test.testDefinition.min_ram_gb

        viable = []
        for hardware in self.machine_management.all_hardware_configs():
            if hardware.cores >= min_cores and hardware.ram_gb >= min_ram_gb and (max_cores <= 0 or hardware.cores <= max_cores):
                viable.append(hardware)

        if not viable:
            return None

        if max_cores > 0:
            #pick the largest number of cores less than or equal to this
            desired_cores = max([v.cores for v in viable])
            viable = [v for v in viable if v.cores == desired_cores]

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

        while env is not None and env.matches.Import:
            commit = self._lookupCommitByHash(env.repo, env.commitHash)
            self._createSourceDep(test, env.repo, env.commitHash)

            if commit and commit.data:
                #this dependency exists already
                env = commit.data.environments.get(env.name, None)
            else:
                env = None

        if env is not None:
            test.fullyResolvedEnvironment = self.database.FullyResolvedTestEnvironment.Resolved(env)
            test.machineCategory = self._machineCategoryForTest(test)

        all_dependencies = {}
        if env is not None and env.matches.Environment:
            all_dependencies.update(env.dependencies)
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
            status=pending
            )

    def _triggerTestPriorityUpdateIfNecessary(self, test):
        if not (self.database.UnresolvedTestDependency.lookupAll(test=test) + 
                self.database.UnresolvedRepoDependency.lookupAll(test=test) +
                self.database.UnresolvedSourceDependency.lookupAll(test=test)
                ):
            self._triggerTestPriorityUpdate(test)

    def _triggerTestPriorityUpdate(self, test):
        self.database.DataTask.New(
            task=self.database.BackgroundTask.UpdateTestPriority(test=test),
            status=pending
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
            repo = self.database.Repo.lookupAny(name=repo)
            if not repo:
                logging.warn("Unknown repo %s", repo)
                return None

        commit = self.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))

        if not commit:
            commit = self.database.Commit.New(repo=repo, hash=commitHash)
            self.database.DataTask.New(
                task=self.database.BackgroundTask.UpdateCommitData(commit=commit),
                status=pending
                )

            for dep in self.database.UnresolvedSourceDependency.lookupAll(repo_and_hash=(repo, commitHash)):
                test = dep.test
                dep.delete()
                self._triggerTestPriorityUpdateIfNecessary(test)


        return commit
