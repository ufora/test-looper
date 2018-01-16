import unittest
import tempfile
import os
import time
import shutil
import logging
import sys
import gzip
import threading

import test_looper_tests.common as common
import test_looper.worker.WorkerState as WorkerState
import test_looper.core.Config as Config
import test_looper.core.tools.Git as Git
import test_looper.core.ArtifactStorage as ArtifactStorage
import test_looper.core.source_control.ReposOnDisk as ReposOnDisk
import test_looper.core.machine_management.MachineManagement as MachineManagement
import test_looper.core.SubprocessRunner as SubprocessRunner
import test_looper.server.TestLooperServer as TestLooperServer
import docker

common.configureLogging()

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

        return (
            source_repo, 
            ReposOnDisk.ReposOnDisk(
                os.path.join(self.testdir,"repo_cache"),
                Config.SourceControlConfig.Local(os.path.join(self.testdir,"repos"))
                ), 
            [(repo_name,c) for c in commits]
            )

    def get_worker(self, repo_name):
        source_repo, source_control, c = self.get_repo(repo_name)
        repoName, commitHash = c[0]

        worker = WorkerState.WorkerState(
            "test_looper_testing",
            os.path.join(self.testdir, "worker"),
            source_control,
            ArtifactStorage.LocalArtifactStorage(
                Config.ArtifactsConfig.LocalDisk(
                    path_to_build_artifacts = os.path.join(self.testdir, "build_artifacts"),
                    path_to_test_artifacts = os.path.join(self.testdir, "test_artifacts")
                    )
                ),
            "worker",
            Config.HardwareConfig(cores=1,ram_gb=4)
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

        fds = len(self.get_fds())
        for i in xrange(10):
            source_repo.hashParentsAndCommitTitleFor(c1)
            source_repo.hashParentsAndCommitTitleFor(c2)
            source_repo.hashParentsAndCommitTitleFor(c3)
        fds2 = len(self.get_fds())

        self.assertEqual(fds, fds2)

    def test_git_copy_dir(self):
        source_repo, source_control, c = self.get_repo("simple_project")
        repo, commitHash = c[0]
        self.assertTrue("ubuntu" in source_repo.getFileContents(commitHash, "Dockerfile.txt"))

    def test_worker_basic(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        result = worker.runTest("testId", repoName, commitHash, "build/linux", WorkerState.DummyWorkerCallbacks(), False)[0]

        self.assertTrue(result)

        self.assertTrue(len(os.listdir(worker.artifactStorage.build_storage_path)) == 1)

        self.assertTrue(
            worker.runTest("testId2", repoName, commitHash, "good/linux", WorkerState.DummyWorkerCallbacks(), False)[0]
            )

        self.assertFalse(
            worker.runTest("testId3", repoName, commitHash, "bad/linux", WorkerState.DummyWorkerCallbacks(), False)[0]
            )

        keys = worker.artifactStorage.testResultKeysFor("testId3")
        self.assertTrue(len(keys) == 1)

        data = worker.artifactStorage.testContents("testId3", keys[0])

        self.assertTrue(len(data) > 0)

    def test_worker_cant_run_tests_without_build(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")
        
        result = worker.runTest("testId", repoName, commitHash, "good/linux", WorkerState.DummyWorkerCallbacks(), False)[0]

        self.assertFalse(result)

    def test_worker_build_artifacts_go_to_correct_place(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")
        
        self.assertTrue(worker.runTest("testId", repoName, commitHash, "build/linux", WorkerState.DummyWorkerCallbacks(), False)[0])

        self.assertTrue(worker.runTest("testId", repoName, commitHash, "check_build_output/linux", WorkerState.DummyWorkerCallbacks(), False)[0])

    def test_worker_doesnt_leak_fds(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        self.assertTrue(worker.runTest("testId", repoName, commitHash, "build/linux", WorkerState.DummyWorkerCallbacks(), False)[0])

        #need to use the connection pools because they can leave some sockets open
        for _ in xrange(3):
            self.assertTrue(worker.runTest("testId2", repoName, commitHash, "good/linux", WorkerState.DummyWorkerCallbacks(), False)[0])

        fds = len(self.get_fds())

        #but want to verify we're not actually leaking FDs once we're in a steadystate
        for _ in xrange(3):
            self.assertTrue(worker.runTest("testId2", repoName, commitHash, "good/linux", WorkerState.DummyWorkerCallbacks(), False)[0])
        
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

        self.assertTrue(worker.runTest("testId", repoName, commitHash, "build/linux", WorkerState.DummyWorkerCallbacks(), False)[0])

        self.assertTrue(
            worker.runTest("testId2", repoName, commitHash, "docker/linux", WorkerState.DummyWorkerCallbacks(), False)[0],
            worker.artifactStorage.get_failure_log("testId2")
            )
        
        self.assertEqual(container_count, len(docker_client.containers.list()))
        
    def test_subdocker_retains_network(self):
        container_count = len(docker_client.containers.list())

        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        self.assertTrue(worker.runTest("testId", repoName, commitHash, "build/linux", WorkerState.DummyWorkerCallbacks(), False)[0])

        self.assertTrue(
            worker.runTest("testId2", repoName, commitHash, "docker/linux", WorkerState.DummyWorkerCallbacks(), False)[0],
            worker.artifactStorage.get_failure_log("testId2")
            )

    def test_cross_project_dependencies(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")
        repo2, _, commit2 = self.get_repo("simple_project_2")
        commit2Name, commit2Hash = commit2[0]

        self.assertTrue(worker.runTest("testId", repoName, commitHash, "build/linux", WorkerState.DummyWorkerCallbacks(), False)[0])

        self.assertTrue(
            worker.runTest("testId2", commit2Name, commit2Hash, "build2/linux", WorkerState.DummyWorkerCallbacks(), False)[0],
            worker.artifactStorage.get_failure_log("testId2")
            )
        self.assertTrue(
            worker.runTest("testId3", commit2Name, commit2Hash, "test2/linux", WorkerState.DummyWorkerCallbacks(), False)[0],
            worker.artifactStorage.get_failure_log("testId3")
            )
        
        self.assertTrue(
            worker.runTest("testId6", commit2Name, commit2Hash, "test2/linux_dependent", WorkerState.DummyWorkerCallbacks(), False)[0],
            worker.artifactStorage.get_failure_log("testId6")
            )


        self.assertTrue(
            worker.runTest("testId7", commit2Name, commit2Hash, "test3/linux_dependent", WorkerState.DummyWorkerCallbacks(), False)[0],
            worker.artifactStorage.get_failure_log("testId7")
            )
        self.assertFalse(
            worker.runTest("testId4", commit2Name, commit2Hash, "test2_fails/linux", WorkerState.DummyWorkerCallbacks(), False)[0]
            )
        self.assertTrue(
            worker.runTest("testId5", commit2Name, commit2Hash, "test2_dep_from_env/linux2", WorkerState.DummyWorkerCallbacks(), False)[0],
            worker.artifactStorage.get_failure_log("testId5")
            )


    def test_deployments(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")
        
        class WorkerCallbacks:
            def __init__(self):
                self.lock = threading.Lock()
                self.pending = []

                self.callback = None
                self.output = ""
                self.done = False

            def heartbeat(self, logMessage=None):
                if self.done:
                    raise Exception("DONE")

            def terminalOutput(self, output):
                self.output += output

            def subscribeToTerminalInput(self, callback):
                with self.lock:
                    self.callback = callback
                    for p in self.pending:
                        callback(p)

            def write(self, msg):
                with self.lock:
                    if self.callback is None:
                        self.pending.append(msg)
                    else:
                        self.callback(msg)
                
        callbacks = WorkerCallbacks()

        def runner():
            worker.runTest("testId", repoName, commitHash, "build/linux", callbacks, isDeploy=True)

        runThread = threading.Thread(target=runner)
        runThread.start()

        try:
            callbacks.write(TestLooperServer.TerminalInputMsg.KeyboardInput("echo 'hi'\n"))

            t0 = time.time()
            while "hi" not in callbacks.output:
                time.sleep(1)
                self.assertTrue(time.time() - t0 < 30.0)
        finally:
            callbacks.done = True
            runThread.join()


    def test_cached_source_builds(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")
        repo2, _, commit2 = self.get_repo("simple_project_2")
        commit2Name, commit2Hash = commit2[0]

        callbacks1 = WorkerState.DummyWorkerCallbacks()
        callbacks2 = WorkerState.DummyWorkerCallbacks()
        callbacks3 = WorkerState.DummyWorkerCallbacks()

        self.assertTrue(
            worker.runTest("testId1", commit2Name, commit2Hash, "test3_dep_on_cached_source/linux", callbacks1, isDeploy=False)[0]
            )

        self.assertTrue(
            worker.runTest("testId2", commit2Name, commit2Hash, "test3_dep_on_cached_source/linux", callbacks2, isDeploy=False)[0]
            )

        logs1 = "\n".join(callbacks1.logMessages)
        logs2 = "\n".join(callbacks2.logMessages)


        test1_uploaded = "Building source cache" in logs1
        test1_downloaded = "Downloading source cache" in logs1
        test1_extracted = "Extracting source cache" in logs1

        test2_uploaded = "Building source cache" in logs2
        test2_downloaded = "Downloading source cache" in logs2
        test2_extracted = "Extracting source cache" in logs2

        self.assertTrue(test1_uploaded and not test1_downloaded and not test1_extracted)
        self.assertTrue(test2_extracted and not test2_downloaded and not test2_uploaded)

        self.assertTrue(
            worker.artifactStorage.build_exists(repoName + "_" + commitHash + "_source.tar.gz"),
            commit2Name + "_" + commit2Hash + "_source.tar.gz"
            )

        #after purging, we should have to download the build
        worker.purge_build_cache(0)

        self.assertTrue(
            worker.runTest("testId3", commit2Name, commit2Hash, "test3_dep_on_cached_source/linux", callbacks3, isDeploy=False)[0]
            )

        logs3 = "\n".join(callbacks3.logMessages)
        
        test3_uploaded = "Building source cache" in logs3
        test3_downloaded = "Downloading source cache" in logs3
        test3_extracted = "Extracting source cache" in logs3

        self.assertTrue(test3_downloaded and test3_extracted and not test3_uploaded)

    def test_summary(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")
        repo2, _, commit2 = self.get_repo("simple_project_2")
        commit2Name, commit2Hash = commit2[0]

        self.assertEqual(
            worker.runTest(
                "testId1", 
                commit2Name, commit2Hash, 
                "test_with_individual_failures/linux", 
                WorkerState.DummyWorkerCallbacks(), 
                isDeploy=False
                )[1],
            {"Test1": True, "Test2": False}
            )
        

        
        


