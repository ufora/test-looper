import unittest
import tempfile
import os
import shutil
import logging
import sys
import simplejson

import test_looper_tests.common as common
import test_looper.data_model.TestManager as TestManager
import test_looper.core.Config as Config
import test_looper.core.machine_management.MachineManagement as MachineManagement
import test_looper.core.InMemoryJsonStore as InMemoryJsonStore
import test_looper.core.tools.Git as Git
import test_looper.core.ArtifactStorage as ArtifactStorage
import test_looper.core.algebraic as algebraic
import test_looper.core.source_control.ReposOnDisk as ReposOnDisk
import test_looper.core.SubprocessRunner as SubprocessRunner
import docker
import threading

own_dir = os.path.split(__file__)[0]

common.configureLogging()

class MockSourceControl:
    def __init__(self):
        self.repos = set()
        self.commit_test_defs = {}
        self.commit_parents = {}
        self.branch_to_commitId = {}
        self.created_commits = 0
        self.prepushHooks = {}

    def listRepos(self):
        return sorted(self.repos)

    def addCommit(self, commitId, parents, testDefs):
        assert len(commitId.split("/")) == 2

        self.repos.add(commitId.split("/")[0])

        for p in parents:
            assert len(p.split("/")) == 2
            assert p.split("/")[0] == commitId.split("/")[0]
            assert p in self.commit_test_defs

        assert commitId not in self.commit_test_defs

        self.commit_test_defs[commitId] = testDefs
        self.commit_parents[commitId] = tuple(parents)

    def getBranch(self, repoAndBranch):
        return self.branch_to_commitId[repoAndBranch]

    def setBranch(self, repoAndBranch, commit):
        if commit is None:
            if repoAndBranch in self.branch_to_commitId:
                del sef.branch_to_commitId[repoAndBranch]
        else:
            assert len(repoAndBranch.split("/")) == 2, "not a valid repo/branch name"
            if "/" not in commit:
                commit = repoAndBranch.split("/")[0] + "/" + commit
            assert len(commit.split("/")) == 2, "not a valid commitId"
            
            assert repoAndBranch.split("/")[0] == commit.split("/")[0], "repos dont match"

            self.branch_to_commitId[repoAndBranch] = commit

    def getRepo(self, repoName):
        if repoName in self.repos:
            return MockRepo(self, repoName)

    def listBranches(self):
        return sorted(list(self.branch_to_commitId))

    def refresh(self):
        pass

class MockGitRepo:
    def __init__(self, repo):
        self.repo = repo

    def fetchOrigin(self):
        pass

    def commitExists(self, branchOrHash):
        return self.repo.commitExists(branchOrHash)

    def standardCommitMessageFor(self, hash):
        assert self.repo.commitExists(hash)

        return "Commit %s by whomever.\n\nThis is a message." % hash

    def getTestDefinitionsPath(self, hash):
        return "testDefinitions.yml"

    def getFileContents(self, commit, path):
        if path != "testDefinitions.yml":
            return None

        commitId = self.repo.repoName + "/" + commit
        return self.repo.source_control.commit_test_defs.get(commitId)


    def createCommitAndPushToBranch(self, target_branch, commitHash, fileContents, commit_message, timestamp_override=None, author="test_looper <test_looper@test_looper.com>"):
        bn = self.repo.repoName + "/" + target_branch

        if bn not in self.repo.source_control.branch_to_commitId:
            return False

        if self.repo.source_control.branch_to_commitId[bn] != self.repo.repoName + "/" + commitHash:
            return False

        assert len(fileContents) == 1 and "testDefinitions.yml" in fileContents

        self.repo.source_control.created_commits += 1
        commitId = self.repo.repoName + "/" + "created_" + str(self.repo.source_control.created_commits)


        self.repo.source_control.commit_parents[commitId] = [self.repo.repoName + "/" + commitHash]
        self.repo.source_control.commit_test_defs[commitId] = fileContents['testDefinitions.yml']

        if bn in self.repo.source_control.prepushHooks:
            #run the test hook
            self.repo.source_control.prepushHooks[bn]()
            del self.repo.source_control.prepushHooks[bn]

            #check again that this is a fast-forward
            if self.repo.source_control.branch_to_commitId[bn] != self.repo.repoName + "/" + commitHash:
                return False

        self.repo.source_control.branch_to_commitId[bn] = commitId

        return True


class MockRepo:
    def __init__(self, source_control, repoName):
        self.source_control = source_control
        self.repoName = repoName
        self.source_repo = MockGitRepo(self)

    def hashParentsAndCommitTitleFor(self, commitId):
        if commitId not in self.source_control.commit_parents:
            raise Exception("Can't find %s in %s" % (commitId, self.source_control.commit_parents.keys()))

        return commitId.split("/")[1], [p.split("/")[1] for p in self.source_control.commit_parents[commitId]], 1516486261, "title"

    def commitExists(self, branchOrHash):
        branchOrHash = self.repoName + "/" + branchOrHash
        branchOrHash = self.source_control.branch_to_commitId.get(branchOrHash, branchOrHash)

        return branchOrHash in self.source_control.commit_parents

    def commitsLookingBack(self, branchOrHash, depth):
        branchOrHash = self.repoName + "/" + branchOrHash
        branchOrHash = self.source_control.branch_to_commitId.get(branchOrHash, branchOrHash)

        tuples = []

        tuples.append(self.hashParentsAndCommitTitleFor(branchOrHash))

        while len(tuples) < depth and len(tuples[-1][1]):
            firstParent = tuples[-1][1][0]
            tuples.append(self.hashParentsAndCommitTitleFor(self.repoName + "/" + firstParent))

        return tuples
    
    def listBranches(self):
        return sorted([b.split("/")[1] for b in self.source_control.branch_to_commitId if b.startswith(self.repoName + "/")])

    def branchTopCommit(self, branch):
        return self.source_control.branch_to_commitId[self.repoName + "/" + branch].split("/")[1]

    def getTestScriptDefinitionsForCommit(self, commitHash):
        assert "/" not in commitHash
        return self.source_control.commit_test_defs[self.repoName + "/" + commitHash], ".yml"

basic_yml_file_repo0 = """
looper_version: 2
"""

basic_yml_file_repo1 = """
looper_version: 2
repos:
  repo0c0: repo0/c0
  repo0c1: repo0/c1
environments:
  linux: 
    platform: linux
    image:
      dockerfile: "test_looper/Dockerfile.txt"
    variables:
      ENV_VAR: LINUX
  windows: 
    platform: windows
    image:
      base_ami: "ami-123"
      setup_script_contents: |
        echo 'ami-contents'
    variables:
      ENV_VAR: WINDOWS
    dependencies:
      dep0: repo0c0
  windows_2:
    base: windows
    variables:
      ENV_VAR: OVERRIDDEN
      ENV_VAR2: WINDOWS_2
    setup_script_contents: |
      echo 'more ami contents'
    dependencies:
      dep1: repo0c1
builds:
  build/linux:
    command: "build.sh"
    min_cores: 1
    max_cores: 1
tests:
  test/linux:
    command: "test.sh"
    dependencies:
      build: build/linux
    min_cores: 4
  test/windows:
    command: "test.py"
"""
basic_yml_file_repo2 = """
looper_version: 2
repos:
  child: repo1/c0
  repo0c0: repo0/c0
  repo0c1: repo0/c1
environments:
  linux: 
    base: child/linux
    variables:
      ENV_VAR_2: LINUX_2
    dependencies:
      dep1: repo0c1
  windows:
    base: child/windows
    variables:
      ENV_VAR_2: WINDOWS_2
    dependencies:
      dep1: repo0c1
  windows_2: 
    base: child/windows_2
    variables:
      ENV_VAR_2: WINDOWS_3
    dependencies:
      dep2: repo0c1
  test_linux:
    platform: linux
    image:
      dockerfile: "test_looper/Dockerfile.txt"
    variables:
      ENV_VAR: ENV_VAL
builds:
  foreach: {env: [linux, test_linux, windows]}
  repeat:
      build/${env}:
        command: "build.sh $TEST_LOOPER_IMPORTS/child"
        dependencies:
          child: child/build/${env}
tests:
  foreach: {env: [linux, test_linux, windows]}
  repeat:
      test/${env}:
        command: "test.sh $TEST_LOOPER_IMPORTS/build"
        dependencies:
          build: build/${env}
"""

basic_yml_file_repo3 = """
looper_version: 2
repos:
  child: repo2/c0
environments:
  linux: 
    base: child/linux
builds:
  build/linux:
    command: "build.sh $TEST_LOOPER_IMPORTS/child"
    dependencies:
      child: child/build/linux
"""

basic_yml_file_repo4 = """
looper_version: 2
environments:
  windows_good: 
    platform: windows
    image:
      base_ami: "ami-123"
  windows_bad: 
    platform: windows
    image:
      base_ami: "not_an_ami"
builds:
  build/windows_good:
    command: "build.sh"
  build/windows_bad:
    command: "build.sh"
"""

basic_yml_file_repo5 = """
looper_version: 2
repos:
  child: 
    reference: repo2/c0
    branch: master
"""

class TestManagerTestHarness:
    def __init__(self, manager):
        self.manager = manager
        self.database = manager.database
        self.timestamp = 1.0
        self.test_record = {}
        self.machine_record = {}

    def add_content(self):
        self.manager.source_control.addCommit("repo0/c0", [], basic_yml_file_repo0)
        self.manager.source_control.addCommit("repo0/c1", ["repo0/c0"], basic_yml_file_repo0)

        self.manager.source_control.addCommit("repo1/c0", [], basic_yml_file_repo1)
        self.manager.source_control.addCommit("repo1/c1", ["repo1/c0"], basic_yml_file_repo1)

        self.manager.source_control.addCommit("repo2/c0", [], basic_yml_file_repo2)
        self.manager.source_control.addCommit("repo2/c1", ["repo2/c0"], basic_yml_file_repo2)

        self.manager.source_control.setBranch("repo0/master", "repo0/c1")
        self.manager.source_control.setBranch("repo1/master", "repo1/c1")
        self.manager.source_control.setBranch("repo2/master", "repo2/c1")

    def markRepoListDirty(self):
        self.manager.markRepoListDirty(self.timestamp)

    def getUnusedMachineId(self):
        with self.manager.database.view():
            for m in self.manager.database.Machine.lookupAll(isAlive=True):
                if not self.manager.database.TestRun.lookupAny(runningOnMachine=m):
                    return m.machineId

    def consumeBackgroundTasks(self):
        cleanedup = False

        while True:
            self.timestamp += 1.0
            task = self.manager.performBackgroundWork(self.timestamp)
            if task is None:
                if not cleanedup:
                    cleanedup=True
                    self.manager.performCleanupTasks(self.timestamp)
                else:
                    return

    def getCommit(self, commitId):
        reponame = "/".join(commitId.split("/")[:-1])
        commitHash = commitId.split("/")[-1]

        repo = self.manager.database.Repo.lookupAny(name=reponame)
        if not repo:
            return

        return self.manager.database.Commit.lookupAny(repo_and_hash=(repo,commitHash))

    def toggleBranchUnderTest(self, reponame, branchname):
        with self.manager.database.transaction():
            b = self.manager.database.Branch.lookupOne(reponame_and_branchname=(reponame,branchname))
            self.manager.toggleBranchUnderTest(b)
        
    def machinesThatRan(self, fullname):
        return [x[0] for x in self.test_record.get(fullname,())]

    def machineConfig(self, machineId):
        with self.manager.database.view():
            m = self.manager.database.Machine.lookupAny(machineId=machineId)
            return (m.hardware, m.os)

    def fullnamesThatRan(self):
        return sorted(self.test_record)

    def assertOneshotMachinesDoOneTest(self):
        for m in self.machine_record:
            os = self.machineConfig(m)[1]
            if os.matches.WindowsVM or os.matches.LinuxVM:
                assert len(self.machine_record[m]) == 1, self.machine_record[m]

    def startAllNewTests(self):
        tests = []
        while len(tests) < 1000:
            machineId = self.getUnusedMachineId()

            if machineId is None:
                return tests

            commitNameAndTest = self.manager.startNewTest(machineId, self.timestamp)

            if commitNameAndTest[0]:
                fullname, testId = ("%s/%s/%s" % commitNameAndTest[:3], commitNameAndTest[3])
                if fullname not in self.test_record:
                    self.test_record[fullname] = []
                self.test_record[fullname].append((machineId, testId))
                if machineId not in self.machine_record:
                    self.machine_record[machineId] = []
                self.machine_record[machineId].append((fullname, testId))

                tests.append(commitNameAndTest)
            else:
                return tests

            self.timestamp

        assert False

    def doTestsInPhases(self):
        counts = []

        while True:
            self.consumeBackgroundTasks()
            tests = self.startAllNewTests()

            if not tests:
                return counts

            counts.append([x[0] + "/" + x[1] + "/" + x[2] for x in tests])

            for _,_,_,testId in tests:
                self.manager.testHeartbeat(testId, self.timestamp)
                self.timestamp += .1

            for _,_,_,testId in tests:
                self.manager.recordTestResults(True, testId, {"ATest":True, "AnotherTest": False}, self.timestamp)
                self.timestamp += .1

FakeConfig = algebraic.Alternative("FakeConfig")
FakeConfig.Config = {"machine_management": Config.MachineManagementConfig}

class TestManagerTests(unittest.TestCase):
    def get_harness(self, max_workers=1000):
        return TestManagerTestHarness(
            TestManager.TestManager(
                MockSourceControl(), 
                MachineManagement.DummyMachineManagement(
                    FakeConfig(
                        machine_management=Config.MachineManagementConfig.Dummy(
                            max_cores=1000,
                            max_ram_gb=1000,
                            max_workers=max_workers
                            )
                        ),
                    None,
                    None
                    ),
                InMemoryJsonStore.InMemoryJsonStore(),
                initialTimestamp = -1000.0
                )
            )

    def test_manager_refresh(self):
        harness = self.get_harness()

        harness.add_content()

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        harness.toggleBranchUnderTest("repo1", "master")
        harness.toggleBranchUnderTest("repo2", "master")
        
        phases = harness.doTestsInPhases()

        self.assertTrue(len(phases) == 3, phases)
        
        self.assertEqual(sorted(phases[0]), sorted([
            "repo1/c1/build/linux",
            "repo1/c0/build/linux",
            "repo1/c1/test/windows",
            "repo1/c0/test/windows"
            ]), phases)

        self.assertEqual(sorted(phases[1]), sorted([
            "repo2/c1/build/linux",
            "repo2/c0/build/linux",
            "repo1/c1/test/linux",
            "repo1/c0/test/linux"
            ]), phases)
        
        self.assertEqual(sorted(phases[2]), sorted([
            "repo2/c1/test/linux",
            "repo2/c0/test/linux"
            ]), phases)

        harness.assertOneshotMachinesDoOneTest()

    def test_manager_branch_pinning(self):
        harness = self.get_harness(max_workers=1)

        harness.add_content()
        
        harness.manager.source_control.addCommit("repo5/c0", [], basic_yml_file_repo5)
        harness.manager.source_control.setBranch("repo5/master", "repo5/c0")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        def branchRefs(branch, ref):
            commitId = harness.manager.source_control.getBranch(branch)

            with harness.database.view():
                commit = harness.getCommit(commitId)
                self.assertEqual(commit.data.repos["child"].reference, ref)

        branchRefs("repo5/master", "repo2/c1")

        #push another commit to repo2
        harness.manager.source_control.addCommit("repo2/c2", ["repo2/c1"], basic_yml_file_repo2)
        harness.manager.source_control.setBranch("repo2/master", "repo2/c2")
        
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        branchRefs("repo5/master", "repo2/c2")

        #push a commit to both repo2
        harness.manager.source_control.addCommit("repo2/c3", ["repo2/c2"], basic_yml_file_repo2)
        harness.manager.source_control.setBranch("repo2/master", "repo2/c3")

        #and also repo5
        harness.manager.source_control.addCommit("repo5/c1", ["repo5/c0"], basic_yml_file_repo5)
        harness.manager.source_control.setBranch("repo5/master", "repo5/c1")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        branchRefs("repo5/master", "repo2/c3")

        #now simulate pushing to c3 failing because we updated a commit

        #update the underlying repo
        harness.manager.source_control.addCommit("repo2/c4", ["repo2/c3"], basic_yml_file_repo2)
        harness.manager.source_control.setBranch("repo2/master", "repo2/c4")

        def beforePush():
            harness.manager.source_control.addCommit("repo5/c2", ["repo5/c1"], basic_yml_file_repo5)
            harness.manager.source_control.setBranch("repo5/master", "repo5/c2")
            
        harness.manager.source_control.prepushHooks["repo5/master"] = beforePush
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        curCommit = harness.manager.source_control.getBranch("repo5/master")
        self.assertEqual(harness.manager.source_control.commit_parents[curCommit][0], "repo5/c2")


    def test_manager_with_one_machine(self):
        harness = self.get_harness(max_workers=1)

        harness.add_content()
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        harness.toggleBranchUnderTest("repo1", "master")
        harness.toggleBranchUnderTest("repo2", "master")
        
        phases = harness.doTestsInPhases()

        self.assertEqual(len(phases), 10)
        harness.assertOneshotMachinesDoOneTest()


    def test_manager_unbootable_hardware_combos(self):
        harness = self.get_harness(max_workers=0)

        harness.manager.source_control.addCommit("repo4/c0", [], basic_yml_file_repo4)
        harness.manager.source_control.setBranch("repo4/master", "repo4/c0")
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()
        
        harness.toggleBranchUnderTest("repo4", "master")
        harness.consumeBackgroundTasks()

        with harness.database.view():
            test1 = harness.database.Test.lookupOne(fullname=("repo4/c0/build/windows_good"))
            test2 = harness.database.Test.lookupOne(fullname=("repo4/c0/build/windows_bad"))

            self.assertTrue(test1.priority.matches.FirstBuild)
            self.assertTrue(test2.priority.matches.HardwareComboUnbootable)


    def test_manager_env_imports(self):
        manager = self.get_harness().manager

        manager.source_control.addCommit("repo0/c0", [], basic_yml_file_repo0)
        manager.source_control.addCommit("repo0/c1", ["repo0/c0"], basic_yml_file_repo0)
        manager.source_control.setBranch("repo0/master", "repo0/c1")

        manager.source_control.addCommit("repo3/c0", [], basic_yml_file_repo3)
        manager.source_control.setBranch("repo3/master", "repo3/c0")

        manager.markRepoListDirty(0.0)

        while manager.performBackgroundWork(0.0) is not None:
            pass

        with manager.database.view():
            repo3 = manager.database.Repo.lookupOne(name="repo3")
            commit3 = manager.database.Commit.lookupOne(repo_and_hash=(repo3, "c0"))
            test3 = manager.database.Test.lookupOne(fullname=("repo3/c0/build/linux"))

            assert test3 is not None
            assert test3.priority.matches.UnresolvedDependencies

        manager.source_control.addCommit("repo2/c0", [], basic_yml_file_repo2)
        manager.source_control.setBranch("repo2/master", "repo2/c0")
        
        manager.markRepoListDirty(0.0)

        while manager.performBackgroundWork(0.0) is not None:
            pass

        with manager.database.view():
            repo2 = manager.database.Repo.lookupOne(name="repo2")
            commit2 = manager.database.Commit.lookupOne(repo_and_hash=(repo2, "c0"))
            test2 = manager.database.Test.lookupOne(fullname=("repo2/c0/build/linux"))

            assert test2 is not None
            assert test2.priority.matches.UnresolvedDependencies

            test2deps = manager.database.UnresolvedRepoDependency.lookupAll(test=test2)
            self.assertEqual([x.reponame + "/" + x.commitHash for x in test2deps], ["repo1/c0"])

            test3deps = manager.database.UnresolvedRepoDependency.lookupAll(test=test3)
            self.assertEqual([x.reponame + "/" + x.commitHash for x in test3deps], ["repo1/c0"])

            assert test3.priority.matches.UnresolvedDependencies, test3.priority
        
        manager.source_control.addCommit("repo1/c0", [], basic_yml_file_repo1)
        manager.source_control.setBranch("repo1/master", "repo1/c0")
        
        manager.markRepoListDirty(0.0)

        while manager.performBackgroundWork(0.0) is not None:
            pass

        with manager.database.view():
            repo1 = manager.database.Repo.lookupOne(name="repo1")
            commit1 = manager.database.Commit.lookupOne(repo_and_hash=(repo1, "c0"))
            test1 = manager.database.Test.lookupOne(fullname=("repo1/c0/build/linux"))

            test2deps = manager.database.UnresolvedSourceDependency.lookupAll(test=test2)
            assert not test2deps, [x.repo.name + "/" + x.commitHash for x in test2deps]

            assert test1 is not None
            assert test1.priority.matches.NoMoreTests
            assert test2.priority.matches.WaitingOnBuilds, test2.priority

            test3deps = manager.database.UnresolvedRepoDependency.lookupAll(test=test3)
            self.assertEqual([x.reponame + "/" + x.commitHash for x in test3deps], [])

            assert test3.priority.matches.WaitingOnBuilds, test3.priority

    def test_manager_timeouts(self):
        harness = self.get_harness()

        harness.add_content()
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()
        harness.toggleBranchUnderTest("repo1", "master")
        
        harness.consumeBackgroundTasks()

        self.assertEqual(len(harness.manager.machine_management.runningMachines), 4)

        commitNameAndTest = harness.manager.startNewTest(harness.getUnusedMachineId(), harness.timestamp)

        with harness.database.view():
            runs = harness.database.TestRun.lookupAll(isRunning=True)
            self.assertEqual(len(runs), 1)
            test = runs[0].test

        harness.timestamp += 500
        harness.consumeBackgroundTasks()

        with harness.database.view():
            self.assertEqual(len(harness.database.TestRun.lookupAll(isRunning=True)), 0)
            self.assertEqual(test.activeRuns, 0)

        harness.timestamp += 500
        harness.consumeBackgroundTasks()

        self.assertEqual(len(harness.manager.machine_management.runningMachines), 4)

        harness.toggleBranchUnderTest("repo1", "master")

        harness.timestamp += 500
        harness.consumeBackgroundTasks()

        #the two windows boxes should still be up
        self.assertEqual(len(harness.manager.machine_management.runningMachines), 2)
        
        harness.timestamp += 5000
        harness.consumeBackgroundTasks()

        self.assertEqual(len(harness.manager.machine_management.runningMachines), 0)
        
        for f in harness.fullnamesThatRan():
            if f.startswith("repo1/build/linux"):
                m = harness.machinesThatRan(f)[0]
                hardware,os = harness.machineConfig(m)

                self.assertTrue(os.matches.LinuxWithDocker)
                self.assertEqual(hardware.cores, 1)

            if f.startswith("repo1/test/linux"):
                m = harness.machinesThatRan(f)[0]
                hardware,os = harness.machineConfig(m)

                self.assertTrue(os.matches.LinuxWithDocker)
                self.assertEqual(hardware.cores, 4)
        
        harness.assertOneshotMachinesDoOneTest()
