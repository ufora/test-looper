import os
import logging
import sys

import test_looper_tests.common as common
import test_looper_tests.TestYamlFiles as TestYamlFiles
import test_looper.data_model.TestManager as TestManager
import test_looper.data_model.TestDefinitionResolver as TestDefinitionResolver
import test_looper.core.Config as Config
import test_looper.core.machine_management.MachineManagement as MachineManagement
import test_looper.core.InMemoryJsonStore as InMemoryJsonStore
import test_looper.core.tools.Git as Git
import test_looper.core.algebraic as algebraic
import test_looper.core.source_control.SourceControl as SourceControl

TestDefinitionResolver.isValidCommitRef = lambda str: str != "HEAD"

class MockSourceControl(SourceControl.SourceControl):
    def __init__(self):
        self.repos = set()
        self.commit_test_defs = {}
        self.commit_parents = {}
        self.commit_message = {}
        self.commit_files = {}
        self.branch_to_commitId = {}
        self.created_commits = 0
        self.prepushHooks = {}
        self.path_to_local_repo_cache = "/repos"

    def clearContents(self):
        self.repos = set()
        self.commit_test_defs = {}
        self.commit_parents = {}
        self.commit_message = {}
        self.branch_to_commitId = {}
        self.created_commits = 0
        self.prepushHooks = {}

    def commit_url(self, repo, hash):
        return "https://scm/%s/%s" % (repo,hash)

    def listRepos(self):
        return sorted(self.repos)

    def addRepo(self, reponame):
        self.repos.add(reponame)

    def addCommit(self, commitId, parents, testDefs, files=None):
        assert len(commitId.split("/")) == 2

        self.repos.add(commitId.split("/")[0])

        for p in parents:
            assert len(p.split("/")) == 2
            assert p.split("/")[0] == commitId.split("/")[0]
            assert p in self.commit_test_defs

        assert commitId not in self.commit_test_defs

        self.commit_test_defs[commitId] = testDefs
        self.commit_parents[commitId] = tuple(parents)
        self.commit_message[commitId] = "title"
        self.commit_files[commitId] = files or {}

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

    def ensureDirectoryExists(self, dirname):
        pass

    def listBranchesForRemote(self, remote):
        if remote != "origin":
            return {}
        res = {}
        for branch, commitId in self.repo.source_control.branch_to_commitId.iteritems():
            if branch.startswith(self.repo.repoName + "/"):
                res[branch[len(self.repo.repoName + "/"):]] = commitId.split("/")[-1]

        return res

    def commitExists(self, branchOrHash):
        return self.repo.commitExists(branchOrHash)

    def mostRecentHashForSubpath(self, commitHash, subpath):
        return commitHash

    def standardCommitMessageFor(self, hash):
        assert self.repo.commitExists(hash)

        return "commit %s\nauthor whomever <whomever@somewhere.com>\n\nThis is a message." % hash

    def getTestDefinitionsPath(self, hash):
        return "testDefinitions.yml"

    def getFileContents(self, commit, path):
        if path == "test_looper/Dockerfile.txt":
            return "fake dockerfile contents"
        commitId = self.repo.repoName + "/" + commit

        if commitId in self.repo.source_control.commit_files:
            if path in self.repo.source_control.commit_files[commitId]:
                return self.repo.source_control.commit_files[commitId][path]

        if path != "testDefinitions.yml":
            return None

        return self.repo.source_control.commit_test_defs.get(commitId)

    def gitCommitData(self, hash):
        return self.repo.getCommitData(self.repo.repoName + "/" + hash)

    def createCommit(self, commitHash, fileContents, commit_message, timestamp_override=None, author="test_looper <test_looper@test_looper.com>"):
        assert len(fileContents) == 1 and "testDefinitions.yml" in fileContents

        self.repo.source_control.created_commits += 1

        assert self.repo.source_control.created_commits < 50, "Created too many new commits for the test to be reasonable"

        newCommitHash = "created_" + str(self.repo.source_control.created_commits)
        newCommitId = self.repo.repoName + "/" + newCommitHash

        self.repo.source_control.commit_message[newCommitId] = commit_message

        self.repo.source_control.commit_parents[newCommitId] = [self.repo.repoName + "/" + commitHash]
        self.repo.source_control.commit_test_defs[newCommitId] = fileContents['testDefinitions.yml']

        return newCommitHash

    def createRepoTarball(self, commitHash, pathWithinRepo, targetTarball, setCoreAutocrlf):
        pass

    def allAncestors(self, c):
        ancestors = set()

        def check(commitId):
            if commitId in ancestors:
                return

            ancestors.add(commitId)

            for child in self.repo.source_control.commit_parents[commitId]:
                check(child)        
        
        check(c)

        return ancestors

    def pushCommit(self, commitHash, target_branch, createBranch=False):
        commitId = self.repo.repoName + "/" + commitHash

        bn = self.repo.repoName + "/" + target_branch

        if createBranch:
            if bn in self.repo.source_control.branch_to_commitId:
                return False
                
            self.repo.source_control.branch_to_commitId[bn] = commitId
            return True

        if bn not in self.repo.source_control.branch_to_commitId:
            return False

        ancestors = self.allAncestors(commitId)

        if self.repo.source_control.branch_to_commitId[bn] not in ancestors:
            logging.error("Can't fast-forward because %s is not an ancestor of %s (%s)", self.repo.source_control.branch_to_commitId[bn], commitId, ancestors)
            return False

        if bn in self.repo.source_control.prepushHooks:
            #run the test hook
            self.repo.source_control.prepushHooks[bn]()
            del self.repo.source_control.prepushHooks[bn]

            #check again that this is a fast-forward
            if self.repo.source_control.branch_to_commitId[bn] not in ancestors:
                logging.error("Can't fast-forward after hook because %s is not an ancestor of %s", self.repo.source_control.branch_to_commitId[bn], commitId)
                return False

        self.repo.source_control.branch_to_commitId[bn] = commitId

        return True


class MockRepo:
    def __init__(self, source_control, repoName):
        self.source_control = source_control
        self.repoName = repoName
        self.source_repo = MockGitRepo(self)

    def getCommitData(self, commitId):
        if commitId not in self.source_control.commit_parents:
            raise Exception("Can't find %s in %s" % (commitId, self.source_control.commit_parents.keys()))

        return (
            commitId.split("/")[1], [p.split("/")[1] for p in self.source_control.commit_parents[commitId]], 1516486261, 
                self.source_control.commit_message[commitId], "author", "author@company"
            )

    def commitExists(self, branchOrHash):
        branchOrHash = self.repoName + "/" + branchOrHash
        branchOrHash = self.source_control.branch_to_commitId.get(branchOrHash, branchOrHash)

        return branchOrHash in self.source_control.commit_parents

    def commitsLookingBack(self, branchOrHash, depth):
        branchOrHash = self.repoName + "/" + branchOrHash
        branchOrHash = self.source_control.branch_to_commitId.get(branchOrHash, branchOrHash)

        tuples = []

        tuples.append(self.getCommitData(branchOrHash))

        while len(tuples) < depth and len(tuples[-1][1]):
            firstParent = tuples[-1][1][0]
            tuples.append(self.getCommitData(self.repoName + "/" + firstParent))

        return tuples
    
    def listBranches(self):
        return sorted([b.split("/")[1] for b in self.source_control.branch_to_commitId if b.startswith(self.repoName + "/")])

    def branchTopCommit(self, branch):
        return self.source_control.branch_to_commitId[self.repoName + "/" + branch].split("/")[1]

    def getTestScriptDefinitionsForCommit(self, commitHash):
        assert "/" not in commitHash
        return self.source_control.commit_test_defs[self.repoName + "/" + commitHash], ".yml"

class TestManagerTestHarness:
    def __init__(self, manager):
        self.manager = manager
        self.database = manager.database
        self.timestamp = 1.0
        self.test_record = {}
        self.machine_record = {}

    def resolver(self):
        return TestDefinitionResolver.TestDefinitionResolver(
            lambda name: self.manager.source_control.getRepo(name).source_repo
            )

    def add_content(self):
        self.manager.source_control.addCommit("repo0/c0", [], TestYamlFiles.repo0)
        self.manager.source_control.addCommit("repo0/c1", ["repo0/c0"], TestYamlFiles.repo0)

        self.manager.source_control.addCommit("repo1/c0", [], TestYamlFiles.repo1)
        self.manager.source_control.addCommit("repo1/c1", ["repo1/c0"], TestYamlFiles.repo1)

        self.manager.source_control.addCommit("repo2/c0", [], TestYamlFiles.repo2)
        self.manager.source_control.addCommit("repo2/c1", ["repo2/c0"], TestYamlFiles.repo2)

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

    def getRepo(self, name):
        return self.database.Repo.lookupAny(name=name)

    def getBranch(self, repo, branch):
        return self.database.Branch.lookupAny(reponame_and_branchname=(repo,branch))

    def getCommit(self, commitId):
        reponame = "/".join(commitId.split("/")[:-1])
        commitHash = commitId.split("/")[-1]

        repo = self.manager.database.Repo.lookupAny(name=reponame)
        if not repo:
            return

        return self.manager.database.Commit.lookupAny(repo_and_hash=(repo,commitHash))

    def enableBranchTesting(self, reponame, branchname):
        with self.manager.database.transaction():
            b = self.manager.database.Branch.lookupOne(reponame_and_branchname=(reponame,branchname))
            self.manager.toggleBranchUnderTest(b)
            self.manager.prioritizeAllCommitsUnderBranch(b, 1, 100)
        
    def disableBranchTesting(self, reponame, branchname):
        with self.manager.database.transaction():
            b = self.manager.database.Branch.lookupOne(reponame_and_branchname=(reponame,branchname))
            if b.isUnderTest:
                self.manager.toggleBranchUnderTest(b)
            self.manager.prioritizeAllCommitsUnderBranch(b, 0, 100)
        
    def machinesThatRan(self, fullname):
        return [x[0] for x in self.test_record.get(fullname,())]

    def machineConfig(self, machineId):
        with self.manager.database.view():
            m = self.manager.database.Machine.lookupAny(machineId=machineId)
            return (m.hardware, m.os)

    def fullnamesThatRan(self):
        return sorted(self.test_record)

    def lookupTestByFullname(self, name):
        repo, commit, testName = name.split("/",2)

        repo=self.manager.database.Repo.lookupAny(name=repo)
        if not repo:
            return None
        
        commit=self.manager.database.Commit.lookupAny(repo_and_hash=(repo,commit))
        if not commit:
            return None

        return commit.data.tests.get(testName)

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

            testId, testDefinition = self.manager.startNewTest(machineId, self.timestamp)

            if testId:
                if testDefinition.hash not in self.test_record:
                    self.test_record[testDefinition.hash] = []
                self.test_record[testDefinition.hash].append((machineId, testId))
                if machineId not in self.machine_record:
                    self.machine_record[machineId] = []
                self.machine_record[machineId].append((testDefinition.hash, testId))

                tests.append((testId, testDefinition))
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

            tail = []

            for testId, testDef in tests:
                with self.manager.database.view():
                    commits = self.manager.commitsReferencingTest(self.manager.database.Test.lookupAny(hash=testDef.hash))
                    if len(commits) == 1:
                        tail.append(commits[0].repo.name + "/" + commits[0].hash + "/" + testDef.name)
                    else:
                        tail.append(testDef.name + "/" + testDef.hash)
            
            counts.append(tail)

            for testId,_ in tests:
                self.manager.testHeartbeat(testId, self.timestamp)
                self.timestamp += .1

            for testId, testDef in tests:
                artifacts = []
                for artifact in [a for stage in testDef.stages for a in stage.artifacts]:
                    self.manager.recordTestArtifactUploaded(testId, artifact.name, self.timestamp, False)
                    artifacts.append(artifact.name)

                self.manager.recordTestResults(True, testId, {"ATest": (True,False), "AnotherTest": (False, False)}, artifacts, self.timestamp)
                self.timestamp += .1

FakeConfig = algebraic.Alternative("FakeConfig")
FakeConfig.Config = {"machine_management": Config.MachineManagementConfig}

def getHarness(max_workers=1000):
    return TestManagerTestHarness(
        TestManager.TestManager(
            None,
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
