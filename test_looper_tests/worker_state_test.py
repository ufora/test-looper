import unittest
import tempfile
import os
import time
import shutil
import logging
import sys
import gzip
import threading
import tarfile
import io

import test_looper_tests.common as common
import test_looper.worker.WorkerState as WorkerState
import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.core.Config as Config
import test_looper.core.tools.Git as Git
import test_looper.core.ArtifactStorage as ArtifactStorage
import test_looper.core.source_control.ReposOnDisk as ReposOnDisk
import test_looper.core.machine_management.MachineManagement as MachineManagement
import test_looper.core.SubprocessRunner as SubprocessRunner
import test_looper.data_model.TestDefinitionResolver as TestDefinitionResolver
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
        self.testIdToTestName = {}
        self.simple_repo_hash = None

    def get_fds(self):
        return os.listdir("/proc/%s/fd" % os.getpid())

    def get_repo_by_name(self, name):
        return Git.Git(os.path.join(self.testdir, "repos", name))

    def uploadSourceTarball(self, worker, repo, commitHash, path, platform):
        worker.artifactStorage.uploadSourceTarball(
            self.get_repo_by_name(repo), commitHash, path, platform
        )

    def callbacksWithUploader(self, worker):
        class WorkerCallbacks(WorkerState.DummyWorkerCallbacks):
            def requestSourceTarballUpload(
                callbacks_self, repoName, commitHash, path, platform
            ):
                self.uploadSourceTarball(worker, repoName, commitHash, path, platform)

        return WorkerCallbacks()

    def get_repo(self, repo_name, extra_commit_paths=None):
        # create a new git repo
        path = os.path.join(self.testdir, "repos", repo_name)
        os.makedirs(path)
        source_repo = Git.Git(path)
        source_repo.init()

        common.mirror_into(
            os.path.join(own_dir, "test_projects", repo_name), source_repo.path_to_repo
        )

        # hashes in simple_repo_1 are not necessarily stable across git revisions
        # so we need to actually paste them in
        if repo_name == "simple_project_2" and self.simple_repo_hash is not None:
            with open(
                os.path.join(source_repo.path_to_repo, "testDefinitions.yml"), "r"
            ) as f:
                content = f.read()
                assert "__replace_this_hash__" in content
                content = content.replace(
                    "__replace_this_hash__", self.simple_repo_hash
                )
            with open(
                os.path.join(source_repo.path_to_repo, "testDefinitions.yml"), "w"
            ) as f:
                f.write(content)

        commits = [source_repo.commit("a message", timestamp)]

        if repo_name == "simple_project" and self.simple_repo_hash is None:
            self.simple_repo_hash = commits[0]

            logging.info("First commit for %s is %s", repo_name, commits[0])

        if extra_commit_paths:
            for commit_ix, bundle in enumerate(extra_commit_paths):
                for fname, data in bundle.items():
                    with open(os.path.join(source_repo.path_to_repo, fname), "w") as f:
                        f.write(data)

                commits.append(
                    source_repo.commit("commit #%s" % (commit_ix + 2), timestamp)
                )

        return (
            source_repo,
            ReposOnDisk.ReposOnDisk(
                os.path.join(self.testdir, "repo_cache"),
                Config.SourceControlConfig.Local(os.path.join(self.testdir, "repos")),
            ),
            [(repo_name, c) for c in commits],
        )

    def get_worker(self, repo_name, autoPullSourceDeps=True):
        source_repo, source_control, c = self.get_repo(repo_name)
        repoName, commitHash = c[0]

        worker = WorkerState.WorkerState(
            "test_looper_testing",
            os.path.join(self.testdir, "worker"),
            ArtifactStorage.LocalArtifactStorage(
                Config.ArtifactsConfig.LocalDisk(
                    path_to_build_artifacts=os.path.join(
                        self.testdir, "build_artifacts"
                    ),
                    path_to_test_artifacts=os.path.join(self.testdir, "test_artifacts"),
                )
            ),
            "worker",
            Config.HardwareConfig(cores=1, ram_gb=4),
        )

        return source_repo, repoName, commitHash, worker

    def get_fully_resolved_definition(
        self, workerState, repoName, commitHash, testName
    ):
        resolver = TestDefinitionResolver.TestDefinitionResolver(self.get_repo_by_name)
        return resolver.testDefinitionsFor(repoName, commitHash)[testName]

    def test_git_not_leaking_fds(self):
        # create a new git repo
        source_repo = Git.Git(os.path.join(self.testdir, "source_repo"))
        source_repo.init()

        source_repo.writeFile("a_file.txt", "contents")
        c1 = source_repo.commit("a message", timestamp)
        source_repo.writeFile("a_file.txt", "contents2")
        c2 = source_repo.commit("a message 2", timestamp)
        source_repo.writeFile("a_file.txt", "contents3")
        c3 = source_repo.commit("a message 3", timestamp)

        fds = len(self.get_fds())
        for i in range(10):
            source_repo.gitCommitData(c1)
            source_repo.gitCommitData(c2)
            source_repo.gitCommitData(c3)
        fds2 = len(self.get_fds())

        self.assertEqual(fds, fds2)

    def test_git_copy_dir(self):
        source_repo, source_control, c = self.get_repo("simple_project")
        repo, commitHash = c[0]
        self.assertTrue(
            "ubuntu" in source_repo.getFileContents(commitHash, "Dockerfile.txt")
        )

    def runWorkerTest(
        self, worker, testId, repoName, commitHash, testName, callbacks, isDeploy
    ):
        test_def = self.get_fully_resolved_definition(
            worker, repoName, commitHash, testName
        )
        self.testIdToTestName[testId] = testName
        return worker.runTest(testId, callbacks, test_def, isDeploy)

    def get_failure_log(self, worker, repoName, commitHash, testId):
        testName = self.testIdToTestName[testId]
        test_def = self.get_fully_resolved_definition(
            worker, repoName, commitHash, testName
        )
        return worker.artifactStorage.get_failure_log(test_def.hash, testId)

    def test_worker_basic(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        result = self.runWorkerTest(
            worker,
            "testId",
            repoName,
            commitHash,
            "build/linux",
            self.callbacksWithUploader(worker),
            False,
        )[0]

        self.assertTrue(result)

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId2",
                repoName,
                commitHash,
                "good/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0]
        )

        self.assertFalse(
            self.runWorkerTest(
                worker,
                "testId3",
                repoName,
                commitHash,
                "bad/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0]
        )

        testHash = self.get_fully_resolved_definition(
            worker, repoName, commitHash, "bad/linux"
        ).hash

        keys = worker.artifactStorage.testResultKeysFor(testHash, "testId3")
        self.assertEqual(
            sorted(keys),
            ["bad_s_linux.tar.gz", "test_looper_log.txt", "test_result.json"],
        )

        data = worker.artifactStorage.testContents(testHash, "testId3", keys[0])

        self.assertTrue(len(data) > 0)

    def test_worker_cant_run_tests_without_build(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        result = self.runWorkerTest(
            worker,
            "testId",
            repoName,
            commitHash,
            "good/linux",
            self.callbacksWithUploader(worker),
            False,
        )[0]

        self.assertFalse(result)

    def test_worker_build_artifacts_go_to_correct_place(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId",
                repoName,
                commitHash,
                "build/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0]
        )

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId2",
                repoName,
                commitHash,
                "check_build_output/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0],
            self.get_failure_log(worker, repoName, commitHash, "testId2"),
        )

    def test_worker_doesnt_leak_fds(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId",
                repoName,
                commitHash,
                "build/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0]
        )

        testIx = 0

        # need to use the connection pools because they can leave some sockets open
        for _ in range(3):
            testIx += 1
            self.assertTrue(
                self.runWorkerTest(
                    worker,
                    "testId%s" % testIx,
                    repoName,
                    commitHash,
                    "good/linux",
                    self.callbacksWithUploader(worker),
                    False,
                )[0]
            )

        fds = len(self.get_fds())

        # but want to verify we're not actually leaking FDs once we're in a steadystate
        for _ in range(3):
            testIx += 1
            self.assertTrue(
                self.runWorkerTest(
                    worker,
                    "testId%s" % testIx,
                    repoName,
                    commitHash,
                    "good/linux",
                    self.callbacksWithUploader(worker),
                    False,
                )[0]
            )

        fds2 = len(self.get_fds())

        self.assertEqual(fds, fds2)

    def test_SR_doesnt_leak(self):
        fds = len(self.get_fds())
        for _ in range(2):
            SubprocessRunner.callAndReturnOutput("ps", shell=True)
        self.assertEqual(len(self.get_fds()), fds)

    def test_doesnt_leak_dockers(self):
        container_count = len(docker_client.containers.list())

        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId",
                repoName,
                commitHash,
                "build/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0]
        )

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId2",
                repoName,
                commitHash,
                "docker/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0],
            self.get_failure_log(worker, repoName, commitHash, "testId2"),
        )

        self.assertEqual(container_count, len(docker_client.containers.list()))

    def test_subdocker_retains_network(self):
        container_count = len(docker_client.containers.list())

        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId",
                repoName,
                commitHash,
                "build/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0]
        )

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId2",
                repoName,
                commitHash,
                "docker/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0],
            self.get_failure_log(worker, repoName, commitHash, "testId2"),
        )

    def test_cross_project_dependencies(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")
        repo2, _, commit2 = self.get_repo("simple_project_2")
        commit2Name, commit2Hash = commit2[0]

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId",
                repoName,
                commitHash,
                "build/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0]
        )

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId2",
                commit2Name,
                commit2Hash,
                "build2/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0],
            self.get_failure_log(worker, commit2Name, commit2Hash, "testId2"),
        )
        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId3",
                commit2Name,
                commit2Hash,
                "test2/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0],
            self.get_failure_log(worker, commit2Name, commit2Hash, "testId3"),
        )

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId6",
                commit2Name,
                commit2Hash,
                "test2/linux_dependent",
                self.callbacksWithUploader(worker),
                False,
            )[0],
            self.get_failure_log(worker, commit2Name, commit2Hash, "testId6"),
        )

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId7",
                commit2Name,
                commit2Hash,
                "test3/linux_dependent",
                self.callbacksWithUploader(worker),
                False,
            )[0],
            self.get_failure_log(worker, commit2Name, commit2Hash, "testId7"),
        )
        self.assertFalse(
            self.runWorkerTest(
                worker,
                "testId4",
                commit2Name,
                commit2Hash,
                "test2_fails/linux",
                self.callbacksWithUploader(worker),
                False,
            )[0]
        )
        self.assertTrue(
            self.runWorkerTest(
                worker,
                "testId5",
                commit2Name,
                commit2Hash,
                "test2_dep_from_env/linux2",
                self.callbacksWithUploader(worker),
                False,
            )[0],
            self.get_failure_log(worker, commit2Name, commit2Hash, "testId5"),
        )

    def test_variable_expansions(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project_3")

        testId = [0]

        def runTest(name):
            testId[0] += 1
            testName = "test_" + str(testId[0])

            callbacks = self.callbacksWithUploader(worker)
            self.assertTrue(
                self.runWorkerTest(
                    worker, testName, repoName, commitHash, name, callbacks, False
                )[0],
                "".join(callbacks.logMessages),
            )

            resolver = TestDefinitionResolver.TestDefinitionResolver(
                self.get_repo_by_name
            )
            testHash = resolver.testDefinitionsFor(repoName, commitHash)[name].hash

            if not name.startswith("build/"):
                contents = worker.artifactStorage.testContents(
                    testHash,
                    testName,
                    worker.artifactStorage.sanitizeName(name + ".tar.gz"),
                )
                with tarfile.open(fileobj=io.BytesIO(contents)) as tf:
                    return [
                        x.strip().decode("ascii")
                        for x in tf.extractfile("./results.txt").read().split(b"\n")
                        if x.strip()
                    ]

        runTest("build/k0")
        runTest("build/k1")
        runTest("build/k2")
        self.assertEqual(runTest("test/env"), ["ENV", "k0"])
        self.assertEqual(runTest("test/env_1"), ["MIXIN_1", "k1"])
        self.assertEqual(runTest("test/env_21"), ["MIXIN_1", "k1"])
        self.assertEqual(runTest("test/env_2"), ["MIXIN_2", "k2"])
        self.assertEqual(runTest("test/env_12"), ["MIXIN_2", "k2"])

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

            def requestSourceTarballUpload(
                callbacks_self, repoName, commitHash, path, platform
            ):
                self.uploadSourceTarball(worker, repoName, commitHash, path, platform)

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

            def recordArtifactUploaded(self, artifact):
                pass

        callbacks = WorkerCallbacks()

        def runner():
            self.runWorkerTest(
                worker,
                "testId",
                repoName,
                commitHash,
                "build/linux",
                callbacks,
                isDeploy=True,
            )

        runThread = threading.Thread(target=runner)
        runThread.start()

        try:
            callbacks.write(
                TestLooperServer.TerminalInputMsg.KeyboardInput("echo 'hi'\n")
            )

            t0 = time.time()
            while "hi" not in callbacks.output:
                time.sleep(1)
                self.assertTrue(time.time() - t0 < 30.0)
        finally:
            callbacks.done = True
            runThread.join()

    def test_summary(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")
        repo2, _, commit2 = self.get_repo("simple_project_2")
        commit2Name, commit2Hash = commit2[0]

        results = self.runWorkerTest(
            worker,
            "testId1",
            commit2Name,
            commit2Hash,
            "test_with_individual_failures/linux",
            self.callbacksWithUploader(worker),
            isDeploy=False,
        )[1]

        self.assertEqual(
            {r.testName: (r.testSucceeded, r.hasLogs) for r in results},
            {"Test1": (True, False), "Test2": (False, False)},
        )

    def test_individual_test_results(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")

        callbacks = self.callbacksWithUploader(worker)

        success, results = self.runWorkerTest(
            worker,
            "testId1",
            repoName,
            commitHash,
            "test_with_individual_failures_1/linux",
            callbacks,
            isDeploy=False,
        )

        self.assertTrue(
            success, self.get_failure_log(worker, repoName, commitHash, "testId1")
        )

        testsWithLogs = [t for t in results if t.hasLogs]

        self.assertTrue(testsWithLogs, "".join(callbacks.logMessages))

        self.get_failure_log(worker, repoName, commitHash, "testId1")

        test_def = self.get_fully_resolved_definition(
            worker, repoName, commitHash, "test_with_individual_failures_1/linux"
        )

        for t in testsWithLogs:
            keysAndSizes = worker.artifactStorage.testResultKeysAndSizesForIndividualTest(
                test_def.hash, "testId1", t.testName, t.testPassIx
            )
            self.assertTrue(keysAndSizes)

            for k, s in keysAndSizes:
                self.assertTrue(
                    worker.artifactStorage.testContentsHtml(test_def.hash, "testId1", k)
                )

    def test_commit_messages(self):
        repo, repoName, commitHash, worker = self.get_worker("simple_project")
        repo2, _, commit2 = self.get_repo("simple_project_2")
        commit2Name, commit2Hash = commit2[0]

        callbacks1 = self.callbacksWithUploader(worker)
        callbacks2 = self.callbacksWithUploader(worker)

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "test0",
                commit2Name,
                commit2Hash,
                "test_commit_message/linux",
                callbacks1,
                isDeploy=False,
            )[0]
        )
        self.assertTrue(
            self.runWorkerTest(
                worker,
                "test2",
                commit2Name,
                commit2Hash,
                "test_commit_message_in_dependencies/linux",
                callbacks2,
                isDeploy=False,
            )[0]
        )

    def test_worker_stage_flow(self):
        repo, repoName, commitHash, worker = self.get_worker("project_with_stages")

        callbacks1 = self.callbacksWithUploader(worker)
        callbacks2 = self.callbacksWithUploader(worker)
        callbacks3 = self.callbacksWithUploader(worker)

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "test0",
                repoName,
                commitHash,
                "build_with_stages",
                callbacks1,
                isDeploy=False,
            )[0],
            self.get_failure_log(worker, repoName, commitHash, "test0"),
        )
        self.assertEqual(callbacks1.artifacts, ["first_stage", "second_stage"])

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "test2",
                repoName,
                commitHash,
                "build_consuming_stage_1",
                callbacks2,
                isDeploy=False,
            )[0]
        )

        self.assertTrue(
            self.runWorkerTest(
                worker,
                "test3",
                repoName,
                commitHash,
                "build_consuming_stage_2",
                callbacks3,
                isDeploy=False,
            )[0]
        )
