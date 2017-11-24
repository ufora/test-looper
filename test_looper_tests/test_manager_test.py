import unittest
import tempfile
import os
import shutil
import logging
import sys
import simplejson

import test_looper_tests.common as common
import test_looper.data_model.TestDatabase as TestDatabase
import test_looper.data_model.TestManager as TestManager
import test_looper.server.RedisJsonStore as RedisJsonStore
import test_looper.core.tools.Git as Git
import test_looper.core.ArtifactStorage as ArtifactStorage
import test_looper.core.source_control.ReposOnDisk as ReposOnDisk
import test_looper.core.cloud.MachineInfo as MachineInfo
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

class MockRepo:
    def __init__(self, source_control, repoName):
        self.source_control = source_control
        self.repoName = repoName

    def hashParentsAndCommitTitleFor(self, commitId):
        if commitId not in self.source_control.commit_parents:
            raise Exception("Can't find %s in %s" % (commitId, self.source_control.commit_parents.keys()))

        return commitId.split("/")[1], [p.split("/")[1] for p in self.source_control.commit_parents[commitId]], "title"

    def commitsLookingBack(self, branchOrHash, depth):
        branchOrHash = self.repoName + "/" + branchOrHash
        branchOrHash = self.source_control.branch_to_commitId.get(branchOrHash, branchOrHash)

        tuples = []

        tuples.append(self.hashParentsAndCommitTitleFor(branchOrHash))

        while len(tuples) < depth and len(tuples[-1][1]):
            firstParent = tuples[-1][1][0]
            tuples.append(self.hashParentsAndCommitTitleFor(self.repoName + "/" + firstParent))

        return tuples
    
    def commitsBetweenBranches(self, branch1, branch2):
        assert False, (branch1, branch2)

    def getTestScriptDefinitionsForCommit(self, commitHash):
        assert "/" not in commitHash
        return simplejson.dumps(self.source_control.commit_test_defs[self.repoName + "/" + commitHash])


basicTestDefs = {
    "looper_version": 1,
    "docker": {
        "dockerfile": "Dockerfile.txt"
        },
    "build": {
        "command": "touch $BUILD_DIR/build_file"
        },
    "tests": [
        {"name": "good", "command": "./script.py 0"},
        {"name": "bad", "command": "./script.py 1"},
        {"name": "docker", "command": "./starts_a_long_docker.py"},
        {"name": "check_build_output", "command": "echo $BUILD_DIR/build_file"}
        ],
    "environments": [
        {"name": "env", 
         "command": "pwd; echo 'hello'",
         "portExpose": "http:8000"
         }
        ]
    }

class TestManagerTests(unittest.TestCase):
    def get_manager(self):
        manager = TestManager.TestManager(
            MockSourceControl(), 
            TestDatabase.TestDatabase(RedisJsonStore.RedisJsonStoreMock(), ""),
            threading.RLock(),
            TestManager.TestManagerSettings(
                "master",
                20,
                3
                )
            )

        return manager

    def test_manager_refresh(self):
        manager = self.get_manager()

        manager.source_control.addCommit("repo1/c0", [], basicTestDefs)
        manager.source_control.addCommit("repo1/c1", ["repo1/c0"], basicTestDefs)
        manager.source_control.addCommit("repo2/c0", [], basicTestDefs)
        manager.source_control.addCommit("repo2/c1", ["repo2/c0"], basicTestDefs)

        manager.source_control.setBranch("repo1/master", "repo1/c1")
        manager.source_control.setBranch("repo2/master", "repo2/c1")

        manager.initialize()

        self.assertEqual(len(manager.branches), 2)
        for b in manager.branches.values():
            self.assertEqual(len(b.commits), 2)