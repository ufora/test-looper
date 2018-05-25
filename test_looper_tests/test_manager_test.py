import unittest
import os
import logging
import textwrap

import test_looper_tests.common as common
import test_looper_tests.TestYamlFiles as TestYamlFiles
import test_looper_tests.TestManagerTestHarness as TestManagerTestHarness
import test_looper.data_model.BranchPinning as BranchPinning
import test_looper.data_model.ImportExport as ImportExport
common.configureLogging()

class TestManagerTests(unittest.TestCase):
    def test_manager_refresh(self):
        harness = TestManagerTestHarness.getHarness()

        harness.add_content()

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        harness.enableBranchTesting("repo1", "master")
        harness.enableBranchTesting("repo2", "master")
        
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

    def test_manager_only_prioritize_repo2(self):
        harness = TestManagerTestHarness.getHarness()

        harness.add_content()

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        harness.enableBranchTesting("repo2", "master")
        
        phases = harness.doTestsInPhases()

        self.assertTrue(len(phases) == 3, phases)
        
        self.assertEqual(sorted(phases[0]), sorted([
            "repo1/c0/build/linux",
            ]), phases)

        self.assertEqual(sorted(phases[1]), sorted([
            "repo2/c0/build/linux",
            "repo2/c1/build/linux"
            ]), phases)

        self.assertEqual(sorted(phases[2]), sorted([
            "repo2/c1/test/linux",
            "repo2/c0/test/linux"
            ]), phases)

        harness.assertOneshotMachinesDoOneTest()

    def test_manager_branch_pinning(self):
        harness = TestManagerTestHarness.getHarness(max_workers=1)

        harness.add_content()
        
        harness.manager.source_control.addCommit("repo5/c0", [], TestYamlFiles.repo5)
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
        harness.manager.source_control.addCommit("repo2/c2", ["repo2/c1"], TestYamlFiles.repo2)
        harness.manager.source_control.setBranch("repo2/master", "repo2/c2")
        
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        branchRefs("repo5/master", "repo2/c2")

        #push a commit to both repo2
        harness.manager.source_control.addCommit("repo2/c3", ["repo2/c2"], TestYamlFiles.repo2)
        harness.manager.source_control.setBranch("repo2/master", "repo2/c3")

        #and also repo5
        harness.manager.source_control.addCommit("repo5/c1", ["repo5/c0"], TestYamlFiles.repo5)
        harness.manager.source_control.setBranch("repo5/master", "repo5/c1")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        branchRefs("repo5/master", "repo2/c3")

        #now simulate pushing to c3 failing because we updated a commit

        #update the underlying repo
        harness.manager.source_control.addCommit("repo2/c4", ["repo2/c3"], TestYamlFiles.repo2)
        harness.manager.source_control.setBranch("repo2/master", "repo2/c4")

        def beforePush():
            harness.manager.source_control.addCommit("repo5/c2", ["repo5/c1"], TestYamlFiles.repo5_nopin)
            harness.manager.source_control.setBranch("repo5/master", "repo5/c2")
            
        harness.manager.source_control.prepushHooks["repo5/master"] = beforePush
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        curCommit = harness.manager.source_control.getBranch("repo5/master")
        
        self.assertEqual(harness.manager.source_control.commit_parents[curCommit][0], "repo5/c1")

    def test_manager_branch_fastforwarding(self):
        harness = TestManagerTestHarness.getHarness(max_workers=1)

        harness.add_content()
        
        harness.manager.source_control.addCommit("repo5/c0", [], TestYamlFiles.repo5)
        harness.manager.source_control.setBranch("repo5/master", "repo5/c0")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        harness.manager.source_control.addCommit("repo2/c2", ["repo2/c1"], TestYamlFiles.repo2)
        harness.manager.source_control.addCommit("repo2/c3", ["repo2/c2"], TestYamlFiles.repo2)
        harness.manager.source_control.addCommit("repo2/c4", ["repo2/c3"], TestYamlFiles.repo2)
        harness.manager.source_control.addCommit("repo2/c5", ["repo2/c4"], TestYamlFiles.repo2)
        harness.manager.source_control.setBranch("repo2/master", "repo2/c5")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.database.view():
            top_commit = harness.getCommit(harness.manager.source_control.getBranch("repo5/master"))
            self.assertEqual(top_commit.data.repos["child"].reference, "repo2/c5")

            repo,branch,hash,refname = BranchPinning.unpackCommitPinUpdateMessage(top_commit.data.commitMessage)
            self.assertEqual(repo, "repo2")
            self.assertEqual(branch, "master")
            self.assertEqual(hash, "c5")

            top_commit = top_commit.data.parents[0]
            self.assertEqual(top_commit.data.repos["child"].reference, "repo2/c4")

            top_commit = top_commit.data.parents[0]
            self.assertEqual(top_commit.data.repos["child"].reference, "repo2/c3")

            top_commit = top_commit.data.parents[0]
            self.assertEqual(top_commit.data.repos["child"].reference, "repo2/c2")

            top_commit = top_commit.data.parents[0]
            self.assertEqual(top_commit.data.repos["child"].reference, "repo2/c1")

        #now push a non-fastforward
        harness.manager.source_control.addCommit("repo2/c2_alt", ["repo2/c1"], TestYamlFiles.repo2)
        harness.manager.source_control.addCommit("repo2/c3_alt", ["repo2/c2_alt"], TestYamlFiles.repo2)
        harness.manager.source_control.addCommit("repo2/c4_alt", ["repo2/c3_alt"], TestYamlFiles.repo2)
        harness.manager.source_control.addCommit("repo2/c5_alt", ["repo2/c4_alt"], TestYamlFiles.repo2)
        harness.manager.source_control.setBranch("repo2/master", "repo2/c5_alt")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.database.view():
            top_commit = harness.getCommit(harness.manager.source_control.getBranch("repo5/master"))
            self.assertEqual(top_commit.data.repos["child"].reference, "repo2/c5_alt")

            top_commit = top_commit.data.parents[0]
            self.assertEqual(top_commit.data.repos["child"].reference, "repo2/c5")

        

    def test_manager_branch_circular_pinning(self):
        harness = TestManagerTestHarness.getHarness(max_workers=1)

        harness.add_content()
        
        harness.manager.source_control.addCommit("repo6/c0", [], TestYamlFiles.repo6.replace("__branch__", "master1"))
        harness.manager.source_control.addCommit("repo6/c1", [], TestYamlFiles.repo6.replace("__branch__", "master2"))
        harness.manager.source_control.addCommit("repo6/c2", [], TestYamlFiles.repo6.replace("__branch__", "master3"))
        harness.manager.source_control.addCommit("repo6/c3", [], TestYamlFiles.repo6.replace("__branch__", "master4"))
        harness.manager.source_control.addCommit("repo6/c4", [], TestYamlFiles.repo6.replace("__branch__", "master0"))

        harness.manager.source_control.setBranch("repo6/master0", "repo6/c0")
        harness.manager.source_control.setBranch("repo6/master1", "repo6/c1")
        harness.manager.source_control.setBranch("repo6/master2", "repo6/c2")
        harness.manager.source_control.setBranch("repo6/master3", "repo6/c3")
        harness.manager.source_control.setBranch("repo6/master4", "repo6/c4")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        self.assertTrue(harness.manager.source_control.created_commits == 0)

        harness.manager.source_control.addCommit("repo6/c0_alt", [], TestYamlFiles.repo6_nopin)
        harness.manager.source_control.setBranch("repo6/master3", "repo6/c0_alt")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        self.assertEqual(harness.manager.source_control.created_commits, 4)


    def test_manager_pin_calculation(self):
        harness = TestManagerTestHarness.getHarness(max_workers=1)

        harness.add_content()
        
        harness.manager.source_control.addCommit("repo6/underlying", [], TestYamlFiles.repo6_nopin)
        harness.manager.source_control.setBranch("repo6/underlying_left", "repo6/underlying")
        harness.manager.source_control.setBranch("repo6/underlying_right", "repo6/underlying")

        harness.manager.source_control.addCommit("repo6/merged", [], 
            (TestYamlFiles.repo6_twopins
                .replace("__branch__", "underlying_left")
                .replace("HEAD1", "root")
                .replace("__branch2__", "underlying_right")
                .replace("HEAD2", "root")
                )
            )
        harness.manager.source_control.setBranch("repo6/merged_branch", "merged")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.database.transaction():
            merged_branch = harness.database.Branch.lookupOne(reponame_and_branchname=("repo6","merged_branch"))
            left_branch = harness.database.Branch.lookupOne(reponame_and_branchname=("repo6","underlying_left"))
            right_branch = harness.database.Branch.lookupOne(reponame_and_branchname=("repo6","underlying_right"))

            merged_branch.isUnderTest = True

        #push to the left branch and nothing happens
        harness.manager.source_control.addCommit("repo6/c1", [], TestYamlFiles.repo6_nopin)
        harness.manager.source_control.setBranch("repo6/underlying_left", "repo6/c1")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.database.transaction():
            self.assertEqual(merged_branch.head.userPriority, 0)

        harness.manager.source_control.addCommit("repo6/c2", [], TestYamlFiles.repo6_nopin)
        harness.manager.source_control.setBranch("repo6/underlying_right", "repo6/c2")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.database.transaction():
            self.assertEqual(merged_branch.head.userPriority, 1)

    
    def test_manager_pin_resolution_ordering(self):
        harness = TestManagerTestHarness.getHarness(max_workers=1)

        harness.add_content()
        
        harness.manager.source_control.addCommit("repo6/underlying", [], TestYamlFiles.repo6_nopin)
        harness.manager.source_control.setBranch("repo6/root", "repo6/underlying")

        nonauto_pin_contents = (
            TestYamlFiles.repo6_headpin
            .replace("__branch__", "root")
            .replace("HEAD", "underlying")
            .replace("true", "false")
            )

        commit_ix = [0]
        def add(branch, deps):
            if len(deps) == 0:
                contents = nonauto_pin_contents
            elif len(deps) == 1:
                contents = (
                    TestYamlFiles.repo6_headpin
                    .replace("__branch__", deps[0])
                    .replace("HEAD", harness.manager.source_control.getBranch("repo6/" + deps[0]).split("/")[1])
                    )
            elif len(deps) == 2:
                contents = (
                    TestYamlFiles.repo6_twopins
                    .replace("__branch__", deps[0])
                    .replace("HEAD1", harness.manager.source_control.getBranch("repo6/" + deps[0]).split("/")[1])
                    .replace("__branch2__", deps[1])
                    .replace("HEAD2", harness.manager.source_control.getBranch("repo6/" + deps[1]).split("/")[1])
                    )
            else:
                assert False

            commitHash = "repo6/c" + str(commit_ix[0])
            harness.manager.source_control.addCommit(commitHash, [], contents)
            harness.manager.source_control.setBranch("repo6/" + branch, commitHash)
            commit_ix[0] += 1

        #build a diamond pattern with a complex web of dependencies
        add("b0", [])
        add("b10", ["b0"])
        add("b11", ["b0"])

        add("b20", ["b10"])
        add("b21", ["b10", "b11"])
        add("b22", ["b11"])

        add("b30", ["b20"])
        add("b31", ["b20", "b21"])
        add("b32", ["b21", "b22"])
        add("b33", ["b22"])

        add("b40", ["b30", "b31"])
        add("b41", ["b31", "b32"])
        add("b42", ["b32", "b33"])

        add("b50", ["b40", "b41"])
        add("b51", ["b41", "b42"])

        add("b60", ["b50", "b51"])

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        harness.manager.source_control.addCommit("repo6/new_base_commit", [], nonauto_pin_contents)
        harness.manager.source_control.setBranch("repo6/b0", "repo6/new_base_commit")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        self.assertEqual(harness.manager.source_control.created_commits, 15)

        harness.manager.source_control.addCommit("repo6/underlying2", [], TestYamlFiles.repo6_nopin)
        harness.manager.source_control.setBranch("repo6/root", "repo6/underlying2")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        #nothing happens
        self.assertEqual(harness.manager.source_control.created_commits, 15)

        with harness.database.transaction():
            branch = harness.database.Branch.lookupOne(reponame_and_branchname=("repo6", "b0"))
            assert branch
            harness.manager._updateBranchPin(branch, "child", False)

        #this should update all 16
        self.assertEqual(harness.manager.source_control.created_commits, 31)

    

    def test_manager_update_head_commits(self):
        harness = TestManagerTestHarness.getHarness(max_workers=1)

        harness.add_content()

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        harness.manager.source_control.addCommit("repo6/c0", [], TestYamlFiles.repo6_nopin)
        harness.manager.source_control.setBranch("repo6/master", "repo6/c0")

        harness.manager.source_control.addCommit("repo6/c1", [], TestYamlFiles.repo6_headpin.replace("__branch__", 'master'))
        harness.manager.source_control.setBranch("repo6/branch2", "repo6/c1")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.database.view():
            commitHash = harness.manager.source_control.getBranch("repo6/branch2")
            top_commit = harness.getCommit(commitHash)
            self.assertEqual(top_commit.data.repos["child"].reference, "repo6/c0")





    def test_manager_with_one_machine(self):
        harness = TestManagerTestHarness.getHarness(max_workers=1)

        harness.add_content()
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        harness.enableBranchTesting("repo1", "master")
        harness.enableBranchTesting("repo2", "master")
        
        phases = harness.doTestsInPhases()

        self.assertEqual(len(phases), 10)
        harness.assertOneshotMachinesDoOneTest()


    def test_manager_unbootable_hardware_combos(self):
        harness = TestManagerTestHarness.getHarness(max_workers=0)

        harness.manager.source_control.addCommit("repo4/c0", [], TestYamlFiles.repo4)
        harness.manager.source_control.setBranch("repo4/master", "repo4/c0")
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()
        
        harness.enableBranchTesting("repo4", "master")
        harness.consumeBackgroundTasks()

        with harness.database.view():
            test1 = harness.lookupTestByFullname(("repo4/c0/build/windows_good"))
            test2 = harness.lookupTestByFullname(("repo4/c0/build/windows_bad"))

            self.assertTrue(test1.priority.matches.FirstBuild)
            self.assertTrue(test2.priority.matches.HardwareComboUnbootable)


    def test_manager_env_imports(self):
        harness = TestManagerTestHarness.getHarness()

        manager = harness.manager

        manager.source_control.addCommit("repo0/c0", [], TestYamlFiles.repo0)
        manager.source_control.addCommit("repo0/c1", ["repo0/c0"], TestYamlFiles.repo0)
        manager.source_control.setBranch("repo0/master", "repo0/c1")

        manager.source_control.addCommit("repo3/c0", [], TestYamlFiles.repo3)
        manager.source_control.setBranch("repo3/master", "repo3/c0")

        manager.markRepoListDirty(0.0)

        while manager.performBackgroundWork(0.0) is not None:
            pass

        with manager.database.view():
            repo3 = manager.database.Repo.lookupOne(name="repo3")
            commit3 = manager.database.Commit.lookupOne(repo_and_hash=(repo3, "c0"))
            test3 = harness.lookupTestByFullname("repo3/c0/build/linux")

            #test doesn't exist yet
            assert test3 is None
            
        manager.source_control.addCommit("repo2/c0", [], TestYamlFiles.repo2)
        manager.source_control.setBranch("repo2/master", "repo2/c0")
        
        manager.markRepoListDirty(0.0)

        while manager.performBackgroundWork(0.0) is not None:
            pass

        with manager.database.view():
            repo2 = manager.database.Repo.lookupOne(name="repo2")
            commit2 = manager.database.Commit.lookupOne(repo_and_hash=(repo2, "c0"))

            test2 = harness.lookupTestByFullname(("repo2/c0/build/linux"))

            assert test2 is None
            
            commit2deps = manager.database.UnresolvedCommitRepoDependency.lookupAll(commit=commit2)
            self.assertEqual([x.reponame for x in commit2deps], ["repo1"])

            commit3deps = manager.database.UnresolvedCommitRepoDependency.lookupAll(commit=commit3)
            self.assertEqual([x.reponame for x in commit3deps], ["repo1"])

        manager.source_control.addCommit("repo1/c0", [], TestYamlFiles.repo1)
        manager.source_control.setBranch("repo1/master", "repo1/c0")
        
        manager.markRepoListDirty(0.0)

        while manager.performBackgroundWork(0.0) is not None:
            pass

        with manager.database.view():
            repo1 = manager.database.Repo.lookupOne(name="repo1")
            commit1 = manager.database.Commit.lookupOne(repo_and_hash=(repo1, "c0"))
            test1 = harness.lookupTestByFullname(("repo1/c0/build/linux"))

            self.assertFalse(manager.database.UnresolvedCommitRepoDependency.lookupAll(commit=commit1))
            self.assertFalse(manager.database.UnresolvedCommitRepoDependency.lookupAll(commit=commit2))
            self.assertFalse(manager.database.UnresolvedCommitRepoDependency.lookupAll(commit=commit3))

            test2 = harness.lookupTestByFullname("repo2/c0/build/linux")
            test3 = harness.lookupTestByFullname("repo3/c0/build/linux")
            
            assert test1 is not None
            assert test2 is not None
            assert test3 is not None

            assert test1.priority.matches.NoMoreTests, test1.priority
            assert test2.priority.matches.WaitingOnBuilds, test2.priority
            assert test3.priority.matches.WaitingOnBuilds, test3.priority

    def test_manager_timeouts(self):
        harness = TestManagerTestHarness.getHarness()

        harness.add_content()
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()
        harness.enableBranchTesting("repo1", "master")
        
        harness.consumeBackgroundTasks()

        self.assertEqual(len(harness.manager.machine_management.runningMachines), 4)

        harness.manager.startNewTest(harness.getUnusedMachineId(), harness.timestamp)

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

        print "Disabling at ", harness.timestamp

        harness.disableBranchTesting("repo1", "master")

        harness.consumeBackgroundTasks()

        harness.timestamp += 500

        for machine in harness.manager.machine_management.runningMachines:
            harness.manager.machineHeartbeat(machine, harness.timestamp)
        
        harness.consumeBackgroundTasks()

        #the two windows boxes should still be up
        self.assertEqual(len(harness.manager.machine_management.runningMachines), 2)
        
        harness.timestamp += 5000
        harness.consumeBackgroundTasks()

        self.assertEqual(len(harness.manager.machine_management.runningMachines), 0)
        
        for f in harness.fullnamesThatRan():
            if f.startswith("build/linux"):
                m = harness.machinesThatRan(f)[0]
                hardware,os = harness.machineConfig(m)

                self.assertTrue(os.matches.LinuxWithDocker)
                self.assertEqual(hardware.cores, 1)

            if f.startswith("test/linux"):
                m = harness.machinesThatRan(f)[0]
                hardware,os = harness.machineConfig(m)

                self.assertTrue(os.matches.LinuxWithDocker)
                self.assertEqual(hardware.cores, 4)
        
        harness.assertOneshotMachinesDoOneTest()

    def test_manager_cancel_orphans(self):
        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo1/c0", [], TestYamlFiles.repo1)
        harness.manager.source_control.addCommit("repo1/c1", [], TestYamlFiles.repo0)
        harness.manager.source_control.setBranch("repo1/master", "repo1/c0")
        
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()
        
        harness.enableBranchTesting("repo1", "master")
        
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()
        
        harness.startAllNewTests()

        def namefor(x):
            return x.testDefinitionSummary.name + "/" + x.hash

        with harness.database.view():
            self.assertEqual(
                sorted([namefor(t.test) for t in harness.database.TestRun.lookupAll(isRunning=True)]), 
                sorted([namefor(harness.lookupTestByFullname(x)) for x in 
                    ["repo1/c0/build/linux","repo1/c0/test/windows"]])
                )

        harness.manager.source_control.setBranch("repo1/master", "repo1/c1")
        
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()
        
        with harness.database.view():
            self.assertEqual(len(harness.database.TestRun.lookupAll(isRunning=True)), 0)
        
    def test_manager_drop_machines_without_heartbeat(self):
        harness = TestManagerTestHarness.getHarness()

        harness.add_content()

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()
        harness.enableBranchTesting("repo1", "master")
        
        harness.consumeBackgroundTasks()

        self.assertEqual(len(harness.manager.machine_management.runningMachines), 4)
        machines = set(harness.manager.machine_management.runningMachines)
            
        harness.timestamp += 200
        harness.consumeBackgroundTasks()

        self.assertTrue(machines == set(harness.manager.machine_management.runningMachines))

        harness.timestamp += 1000
        harness.consumeBackgroundTasks()

        self.assertTrue(machines != set(harness.manager.machine_management.runningMachines))
        
    def test_manager_remembers_old_repos(self):
        harness = TestManagerTestHarness.getHarness()

        harness.add_content()

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.database.view():
            self.assertTrue(harness.database.Repo.lookupAll(isActive=True))
            repo1 = harness.database.Repo.lookupOne(name='repo1')

        harness.enableBranchTesting("repo1", "master")

        phases = harness.doTestsInPhases()

        harness.manager.source_control.clearContents()
        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        #verify we still have test runs
        with harness.database.view():
            self.assertFalse(harness.database.Repo.lookupAll(isActive=True))
            self.assertEqual(repo1._identity, harness.database.Repo.lookupOne(name='repo1')._identity)

            c0 = harness.database.Commit.lookupOne(repo_and_hash=(repo1,"c0"))
            self.assertTrue(harness.manager.allTestsForCommit(c0))
            harness.manager.allTestsForCommit(c0)[0].totalRuns

    def test_manager_missing_environment_refs(self):
        def add(harness, whichRepo):
            if whichRepo == 0:
                harness.manager.source_control.addCommit("repo0/c0", [], TestYamlFiles.repo0)
                harness.manager.source_control.addCommit("repo0/c1", ['repo0/c0'], TestYamlFiles.repo0)
                harness.manager.source_control.setBranch("repo0/master", "repo0/c1")
            if whichRepo == 1:
                harness.manager.source_control.addCommit("repo1/c0", [], TestYamlFiles.repo1)
                harness.manager.source_control.setBranch("repo1/master", "repo1/c0")
            if whichRepo == 2:
                harness.manager.source_control.addCommit("repo2/c0", [], 
                    TestYamlFiles.repo2.replace("disabled: true", "disabled: false"))
                harness.manager.source_control.setBranch("repo2/master", "repo2/c0")
            if whichRepo == 3:
                harness.manager.source_control.addCommit("repo3/c0", [], 
                    TestYamlFiles.repo3.replace("disabled: true", "disabled: false"))
                harness.manager.source_control.setBranch("repo3/master", "repo3/c0")

        for ordering in [
                    (0,1,2,3), 
                    (3,2,1,0), 
                    (3,1,2,0)
                    ]:
            harness = TestManagerTestHarness.getHarness()

            #make sure it knows about all the repos
            for reponumber in xrange(4):
                harness.manager.source_control.addRepo("repo%s" % reponumber)
                    
            for r in ordering:
                add(harness, r)
                harness.markRepoListDirty()
                harness.consumeBackgroundTasks()

            with harness.database.view():
                self.assertTrue(harness.lookupTestByFullname("repo2/c0/build_without_deps/linux"), ordering)
                self.assertTrue(harness.lookupTestByFullname("repo3/c0/build_without_deps/linux"), ordering)
                


    def test_circular_environment_refs(self):
        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo7/c0", [], TestYamlFiles.repo7_circular)
        harness.manager.source_control.setBranch("repo7/master", "repo7/c0")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.database.view():
            self.assertFalse(harness.lookupTestByFullname("repo7/c0/build"))
            
    def test_circular_test_refs(self):
        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo8/c0", [], TestYamlFiles.repo8_circular_builds)
        harness.manager.source_control.setBranch("repo8/master", "repo8/c0")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.database.view():
            c = harness.getCommit("repo8/c0")
            self.assertFalse(harness.lookupTestByFullname("repo8/c0/build1/e1"))

            self.assertTrue("ircular" in c.data.testDefinitionsError, c.data.testDefinitionsError)

    def test_manager_import_export(self):
        harness = TestManagerTestHarness.getHarness()

        harness.add_content()

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        harness.enableBranchTesting("repo1", "master")
        harness.enableBranchTesting("repo2", "master")
        
        phases = harness.doTestsInPhases()

        exporter = ImportExport.ImportExport(harness.manager)
        jsonRepresentation = exporter.export()

        harness2 = TestManagerTestHarness.getHarness()
        harness2.add_content()

        importer = ImportExport.ImportExport(harness2.manager)
        self.assertFalse(importer.importResults(jsonRepresentation))

        harness2.markRepoListDirty()
        harness2.consumeBackgroundTasks()

        self.assertEqual(importer.export(), jsonRepresentation)

    def test_child_repo_refs(self):
        harness = TestManagerTestHarness.getHarness()

        harness.add_content()
        harness.manager.source_control.addCommit("repo9/c0", [], TestYamlFiles.repo9_import_child_refs)
        harness.manager.source_control.setBranch("repo9/master", "repo9/c0")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.database.view():
            c = harness.getCommit("repo9/c0")
            test0 = harness.lookupTestByFullname("repo9/c0/build/repo0_env")
            test1 = harness.lookupTestByFullname("repo9/c0/build/repo1_env")
            test2 = harness.lookupTestByFullname("repo9/c0/build/repo2_env")
       
            self.assertTrue(test0)
            self.assertTrue(test1)
            self.assertTrue(test2) 

            self.assertEqual(harness.manager.environmentForTest(test0).variables['ENV'], "repo0")
            self.assertEqual(harness.manager.environmentForTest(test1).variables['ENV_VAR'], "LINUX")
            self.assertEqual(harness.manager.environmentForTest(test2).variables['ENV_VAR_2'], "LINUX_2")


    def test_manager_branch_creation_from_template(self):
        harness = TestManagerTestHarness.getHarness(max_workers=1)

        harness.add_content()
        
        harness.manager.source_control.addCommit("repo6/c0", [], "")
        harness.manager.source_control.addCommit("repo6/c1", ["repo6/c0"], "")
        harness.manager.source_control.setBranch("repo6/master", "repo6/c0")

        harness.manager.source_control.addCommit("repo6/c0_test", [], 
            textwrap.dedent("""
            looper_version: 4
            repos:
              child: 
                reference: repo6/c0
                branch: master
                auto: true
              other_child: 
                reference: repo6/c0
                branch: something_random
                auto: true
            """))
        harness.manager.source_control.setBranch("repo6/master-looper", "repo6/c0_test")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.manager.transaction_and_lock():
            repo = harness.getRepo("repo6")
            repo.branchCreateTemplates = [
                harness.database.BranchCreateTemplate.New(
                    globsToInclude=["*"],
                    globsToExclude=["*master*", "*-looper"],
                    suffix="-looper",
                    branchToCopyFrom="master-looper",
                    def_to_replace="child",
                    disableOtherAutos=True
                    )
                ]

        harness.manager.source_control.setBranch("repo6/a_branch", "repo6/c1")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.database.view():
            print
            log = harness.getRepo("repo6").branchCreateLogs
            while log:
                print log.msg
                log = log.prior

        self.assertTrue("repo6/a_branch-looper" in harness.manager.source_control.listBranches(),
            harness.manager.source_control.listBranches()
            )
