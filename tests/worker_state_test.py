import unittest
import tempfile
import os
import shutil
import logging
import sys
import gzip

import test_looper.worker.WorkerState as WorkerState
import test_looper.core.tools.Git as Git
import test_looper.core.ArtifactStorage as ArtifactStorage
import test_looper.core.source_control.LocalGitRepo as LocalGitRepo
import test_looper.core.cloud.MachineInfo as MachineInfo
import test_looper.core.SubprocessRunner as SubprocessRunner
import docker

docker_client = docker.from_env()
docker_client.containers.list()


own_dir = os.path.split(__file__)[0]

def configureLogging(verbose=False):
    if logging.getLogger().handlers:
        logging.getLogger().handlers = []

    loglevel = logging.INFO if verbose else logging.ERROR
    logging.getLogger().setLevel(loglevel)

    handler = logging.StreamHandler(stream=sys.stderr)

    handler.setLevel(loglevel)
    handler.setFormatter(
        logging.Formatter(
            '%(asctime)s %(levelname)s %(filename)s:%(lineno)s@%(funcName)s %(name)s - %(message)s'
            )
        )
    logging.getLogger().addHandler(handler)

configureLogging()

def mirror_into(src_dir, dest_dir):
    for p in os.listdir(src_dir):
        if os.path.isdir(p):
            if os.path.exists(os.path.join(dest_dir, p)):
                shutil.rmtree(os.path.join(dest_dir, p))
            shutil.copytree(os.path.join(src_dir, p), os.path.join(dest_dir, p), symlinks=True)
        else:
            shutil.copy2(os.path.join(src_dir, p), os.path.join(dest_dir, p))
    for p in os.listdir(dest_dir):
        if not os.path.exists(os.path.join(src_dir, p)) and not p.startswith("."):
            if os.path.isfile(os.path.join(src_dir, p)):
                os.remove(os.path.join(src_dir, p))
            else:
                shutil.rmtree(os.path.join(src_dir, p))

class WorkerStateTests(unittest.TestCase):
    def setUp(self):
        self.testdir = tempfile.mkdtemp()

    def get_fds(self):
        return os.listdir("/proc/%s/fd" % os.getpid())

    def get_repo(self, repo_name):
        #create a new git repo
        source_repo = Git.Git(os.path.join(self.testdir, "source_repo"))
        source_repo.init()
        
        mirror_into(
            os.path.join(own_dir,"test_projects", repo_name), 
            source_repo.path_to_repo
            )

        c = source_repo.commit("a message")

        return source_repo, c

    def get_worker(self, repo_name):
        source_repo, c = self.get_repo(repo_name)

        worker = WorkerState.WorkerState(
            os.path.join(self.testdir, "worker"),
            source_repo, 
            "testDefinitions.json",
            ArtifactStorage.LocalArtifactStorage({
                "build_storage_path": os.path.join(self.testdir, "build_artifacts"),
                "test_artifacts_storage_path": os.path.join(self.testdir, "test_artifacts")
                }),
            MachineInfo.MachineInfo("worker1", "worker1.ip", 4, "worker_zone", "worker_machine_type")
            )

        return source_repo, c, worker


    def test_git_not_leaking_fds(self):
        #create a new git repo
        source_repo = Git.Git(os.path.join(self.testdir, "source_repo"))
        source_repo.init()

        source_repo.writeFile("a_file.txt", "contents")
        c1 = source_repo.commit("a message")
        source_repo.writeFile("a_file.txt", "contents2")
        c2 = source_repo.commit("a message 2")
        source_repo.writeFile("a_file.txt", "contents3")
        c3 = source_repo.commit("a message 3")

        revs = [x[0] for x in source_repo.commitsInRevList("HEAD ^HEAD^^")]
        self.assertEqual(revs, [c3,c2])

        fds = len(self.get_fds())
        for i in xrange(10):
            source_repo.commitsInRevList("HEAD ^HEAD^^")
        fds2 = len(self.get_fds())

        self.assertEqual(fds, fds2)

    def test_git_copy_dir(self):
        source_repo, c = self.get_repo("simple_project")
        self.assertTrue("ubuntu" in source_repo.getFileContents(c, "Dockerfile.txt"))

    def test_worker_basic(self):
        repo, commit, worker = self.get_worker("simple_project")
        
        result = worker.runTest("testId", commit, "build", lambda *args: None)

        self.assertTrue(result.success)

        self.assertTrue(len(os.listdir(worker.artifactStorage.build_storage_path)) == 1)

        self.assertTrue(
            worker.runTest("testId2", commit, "good", lambda *args: None).success
            )

        self.assertFalse(
            worker.runTest("testId3", commit, "bad", lambda *args: None).success
            )

        keys = worker.artifactStorage.testResultKeysFor("testId3")
        self.assertTrue(len(keys) == 1)

        data = worker.artifactStorage.testContents("testId3", keys[0])

        self.assertTrue(len(data) > 0)

    def test_worker_cant_run_tests_without_build(self):
        repo, commit, worker = self.get_worker("simple_project")
        
        result = worker.runTest("testId", commit, "good", lambda *args: None)

        self.assertFalse(result.success)

    def test_worker_build_artifacts_go_to_correct_place(self):
        repo, commit, worker = self.get_worker("simple_project")
        
        result = worker.runTest("testId", commit, "check_build_output", lambda *args: None)

        self.assertFalse(result.success)

    def test_worker_doesnt_leak_fds(self):
        repo, commit, worker = self.get_worker("simple_project")

        result = worker.runTest("testId", commit, "build", lambda *args: None)

        #need to use the connection pools because they can leave some sockets open
        for _ in xrange(3):
            worker.runTest("testId2", commit, "good", lambda *args: None)

        fds = len(self.get_fds())

        #but want to verify we're not actually leaking FDs once we're in a steadystate
        for _ in xrange(3):
            worker.runTest("testId2", commit, "good", lambda *args: None)
        
        fds2 = len(self.get_fds())
        
        self.assertEqual(fds, fds2)

    def test_SR_doesnt_leak(self):
        fds = len(self.get_fds())
        for _ in xrange(2):
            SubprocessRunner.callAndReturnOutput("ps",shell=True)
        self.assertEqual(len(self.get_fds()), fds)

    def test_doesnt_leak_dockers(self):
        container_count = len(docker_client.containers.list())

        repo, commit, worker = self.get_worker("simple_project")

        worker.runTest("testId", commit, "build", lambda *args: None)

        self.assertTrue(
            worker.runTest("testId2", commit, "docker", lambda *args: None).success,
            worker.get_failure_log("testId2")
            )
        
        self.assertEqual(container_count, len(docker_client.containers.list()))
        
    def test_subdocker_retains_network(self):
        container_count = len(docker_client.containers.list())

        repo, commit, worker = self.get_worker("simple_project")

        self.assertTrue(worker.runTest("testId", commit, "build", lambda *args: None).success)

        
        self.assertTrue(
            worker.runTest("testId2", commit, "docker", lambda *args: None).success,
            worker.get_failure_log("testId2")
            )