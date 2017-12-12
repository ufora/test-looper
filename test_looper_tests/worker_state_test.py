import unittest
import tempfile
import os
import shutil
import logging
import sys
import gzip

import test_looper_tests.common as common
import test_looper.worker.WorkerState as WorkerState
import test_looper.core.tools.Git as Git
import test_looper.core.ArtifactStorage as ArtifactStorage
import test_looper.core.source_control.ReposOnDisk as ReposOnDisk
import test_looper.core.cloud.MachineInfo as MachineInfo
import test_looper.core.SubprocessRunner as SubprocessRunner
import docker

docker_client = docker.from_env()
docker_client.containers.list()

own_dir = os.path.split(__file__)[0]

timestamp = 1512679665

class WorkerStateTests(unittest.TestCase):
    def setUp(self):
        common.configureLogging(verbose=True)
        logging.info("WorkerStateTests set up")
        self.testdir = tempfile.mkdtemp()

    def get_fds(self):
        return os.listdir("/proc/%s/fd" % os.getpid())

    def get_repo(self, repo_name, extra_commit_paths=None):
        #create a new git repo
        path = os.path.join(self.testdir, "repos", repo_name)
        os.makedirs(path)
        source_repo = Git.Git(path)
        source_repo.init()
        
        common.mirror_into(
            os.path.join(own_dir,"test_projects", repo_name), 
            source_repo.path_to_repo
            )

        commits = [source_repo.commit("a message", timestamp)]

        logging.info("First commit for %s is %s", repo_name, commits[0])

        if extra_commit_paths:
            for commit_ix, bundle in enumerate(extra_commit_paths):
                for fname, data in bundle.iteritems():
                    with open(os.path.join(source_repo.path_to_repo, fname), "w") as f:
                        f.write(data)

                commits.append(source_repo.commit("commit #%s" % (commit_ix + 2), timestamp))

        return source_repo, ReposOnDisk.ReposOnDisk(os.path.join(self.testdir,"repos")), [(repo_name,c) for c in commits]

    def get_worker(self, repo_name):
        source_repo, source_control, c = self.get_repo(repo_name)
        repoName, commitHash = c[0]

        worker = WorkerState.WorkerState(
            "test_looper_testing",
            os.path.join(self.testdir, "worker"),
            source_control,
            ArtifactStorage.LocalArtifactStorage({
                "build_storage_path": os.path.join(self.testdir, "build_artifacts"),
                "test_artifacts_storage_path": os.path.join(self.testdir, "test_artifacts")
                }),
            MachineInfo.MachineInfo("worker1", "worker1.ip", 4, "worker_zone", "worker_machine_type")
            )

        return source_repo, repoName, commitHash, worker

    def test_git_not_leaking_fds(self):
        #create a new git repo
        source_repo = Git.Git(os.path.join(self.testdir, "source_repo"))
        source_repo.init()

        source_repo.writeFile("a_file.txt", "contents")
        c1 = source_repo.commit("a message", timestamp)
        source_repo.writeFile("a_file.txt", "contents2")
        c2 = source_repo.commit("a message 2", timestamp)
        source_repo.writeFile("a_file.txt", "contents3")
        c3 = source_repo.commit("a message 3", timestamp)

        revs = [x[0] for x in source_repo.commitsInRevList("HEAD ^HEAD^^")]
        self.assertEqual(revs, [c3,c2])

        fds = len(self.get_fds())
        for i in xrange(10):
            source_repo.commitsInRevList("HEAD ^HEAD^^")
        fds2 = len(self.get_fds())

        self.assertEqual(fds, fds2)

    def test_git_copy_dir(self):
        source_repo, source_control, c = self.get_repo("simple_project")
        repo, commitHash = c[0]
        self.assertTrue("ubuntu" in source_repo.getFileContents(commitHash, "Dockerfile.txt"))

    def test_worker_basic(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        result = worker.runTest("testId", repoName, commitHash, "build/linux", lambda *args: None)

        self.assertTrue(result.success)

        self.assertTrue(len(os.listdir(worker.artifactStorage.build_storage_path)) == 1)

        self.assertTrue(
            worker.runTest("testId2", repoName, commitHash, "good/linux", lambda *args: None).success
            )

        self.assertFalse(
            worker.runTest("testId3", repoName, commitHash, "bad/linux", lambda *args: None).success
            )

        keys = worker.artifactStorage.testResultKeysFor("testId3")
        self.assertTrue(len(keys) == 1)

        data = worker.artifactStorage.testContents("testId3", keys[0])

        self.assertTrue(len(data) > 0)

    def test_worker_cant_run_tests_without_build(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")
        
        result = worker.runTest("testId", repoName, commitHash, "good/linux", lambda *args: None)

        self.assertFalse(result.success)

    def test_worker_build_artifacts_go_to_correct_place(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")
        
        self.assertTrue(worker.runTest("testId", repoName, commitHash, "build/linux", lambda *args: None).success)

        self.assertTrue(worker.runTest("testId", repoName, commitHash, "check_build_output/linux", lambda *args: None).success)

    def test_worker_doesnt_leak_fds(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        self.assertTrue(worker.runTest("testId", repoName, commitHash, "build/linux", lambda *args: None).success)

        #need to use the connection pools because they can leave some sockets open
        for _ in xrange(3):
            self.assertTrue(worker.runTest("testId2", repoName, commitHash, "good/linux", lambda *args: None).success)

        fds = len(self.get_fds())

        #but want to verify we're not actually leaking FDs once we're in a steadystate
        for _ in xrange(3):
            self.assertTrue(worker.runTest("testId2", repoName, commitHash, "good/linux", lambda *args: None).success)
        
        fds2 = len(self.get_fds())
        
        self.assertEqual(fds, fds2)

    def test_SR_doesnt_leak(self):
        fds = len(self.get_fds())
        for _ in xrange(2):
            SubprocessRunner.callAndReturnOutput("ps",shell=True)
        self.assertEqual(len(self.get_fds()), fds)

    def test_doesnt_leak_dockers(self):
        container_count = len(docker_client.containers.list())

        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        self.assertTrue(worker.runTest("testId", repoName, commitHash, "build/linux", lambda *args: None).success)

        self.assertTrue(
            worker.runTest("testId2", repoName, commitHash, "docker/linux", lambda *args: None).success,
            worker.get_failure_log("testId2")
            )
        
        self.assertEqual(container_count, len(docker_client.containers.list()))
        
    def test_subdocker_retains_network(self):
        container_count = len(docker_client.containers.list())

        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        self.assertTrue(worker.runTest("testId", repoName, commitHash, "build/linux", lambda *args: None).success)

        self.assertTrue(
            worker.runTest("testId2", repoName, commitHash, "docker/linux", lambda *args: None).success,
            worker.get_failure_log("testId2")
            )

    def test_cross_project_dependencies(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")
        repo2, _, commit2 = self.get_repo("simple_project_2")
        commit2Name, commit2Hash = commit2[0]

        self.assertTrue(worker.runTest("testId", repoName, commitHash, "build/linux", lambda *args: None).success)        
        self.assertTrue(
            worker.runTest("testId2", commit2Name, commit2Hash, "build2/linux", lambda *args: None).success,
            worker.get_failure_log("testId2")
            )
        self.assertTrue(
            worker.runTest("testId3", commit2Name, commit2Hash, "test2/linux", lambda *args: None).success,
            worker.get_failure_log("testId3")
            )
        self.assertFalse(
            worker.runTest("testId4", commit2Name, commit2Hash, "test2_fails/linux", lambda *args: None).success
            )
        self.assertTrue(
            worker.runTest("testId5", commit2Name, commit2Hash, "test2_dep_from_env/linux2", lambda *args: None).success,
            worker.get_failure_log("testId5")
            )

