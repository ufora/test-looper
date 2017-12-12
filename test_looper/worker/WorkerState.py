import collections
import errno
import logging
import os
import shutil
import signal
import simplejson
import sys
import tarfile
import threading
import time
import requests
import traceback
import virtualenv
import psutil
import tempfile

import test_looper.core.SubprocessRunner as SubprocessRunner
import test_looper.core.tools.Git as Git
import test_looper.core.DirectoryScope as DirectoryScope
import test_looper.worker.TestLooperClient as TestLooperClient
import test_looper.core.tools.Docker as Docker
import test_looper.core.tools.DockerWatcher as DockerWatcher
import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.data_model.TestResult as TestResult
import test_looper

class TestLooperDirectories:
    def __init__(self, worker_directory):
        self.repo_cache = os.path.join(worker_directory, "repos")
        self.repo_copy_dir = os.path.join(worker_directory, "repo_copy")
        self.scratch_dir = os.path.join(worker_directory, "scratch_dir")
        self.test_inputs_dir = os.path.join(worker_directory, "test_inputs")
        self.test_output_dir = os.path.join(worker_directory, "test_output")
        self.build_output_dir = os.path.join(worker_directory, "build_output")
        self.test_data_dir = os.path.join(worker_directory, "test_data")
        self.build_cache_dir = os.path.join(worker_directory, "build_cache")
        self.ccache_dir = os.path.join(worker_directory, "ccache")

    def all(self):
        return [self.repo_copy_dir, self.scratch_dir, self.test_inputs_dir, self.test_data_dir, 
                self.build_cache_dir, self.ccache_dir, self.test_output_dir, self.build_output_dir, self.repo_cache]

class WorkerState(object):
    def __init__(self, name_prefix, worker_directory, source_control, artifactStorage, machineInfo, timeout=900, verbose=False):
        import test_looper.worker.TestLooperWorker

        self.name_prefix = name_prefix

        assert isinstance(worker_directory, (str,unicode)), worker_directory
        worker_directory = str(worker_directory)

        self.worker_directory = worker_directory

        self.verbose = verbose

        self.timeout = timeout

        self.directories = TestLooperDirectories(worker_directory)

        self.repos_by_name = {}

        self.machineInfo = machineInfo

        for path in self.directories.all():
            self.ensureDirectoryExists(path)

        self.max_build_cache_depth = 10

        self.heartbeatInterval = TestLooperClient.TestLooperClient.HEARTBEAT_INTERVAL

        self.artifactStorage = artifactStorage

        self.source_control = source_control

        self.cleanup()

    def getRepoCacheByName(self, name):
        if name not in self.repos_by_name:
            self.repos_by_name[name] = Git.Git(str(os.path.join(self.directories.repo_cache, name)))

            if not self.repos_by_name[name].isInitialized():
                self.repos_by_name[name].cloneFrom(self.source_control.getRepo(name).cloneUrl())

        return self.repos_by_name[name]


    def untarString(self, contents):
        tempdir = tempfile.mkdtemp()

        with open(os.path.join(tempdir, "out.log.gz"), "wb") as f:
            f.write(contents)

        args = "gunzip %s" % f.name

        res,out = SubprocessRunner.callAndReturnResultAndMergedOutput(args,shell=True)
        if res != 0:
            assert False

        for f in os.listdir(tempdir):
            with open(os.path.join(tempdir,f),"r") as textfile:
                return textfile.read()

    def get_failure_log(self, testId):
        keys = self.artifactStorage.testResultKeysFor(testId)
        for k in keys:
            res = self.artifactStorage.testContents(testId, k)
            return self.untarString(res)

    @staticmethod
    def directoryScope(directoryScope):
        return DirectoryScope.DirectoryScope(directoryScope)

    def cleanup(self):
        Docker.DockerImage.removeDanglingDockerImages()
        self.clearDirectoryAsRoot(
            self.directories.test_data_dir, 
            self.directories.test_output_dir,
            self.directories.build_output_dir,
            self.directories.scratch_dir, 
            self.directories.test_inputs_dir, 
            self.directories.repo_copy_dir
            )
        
    @staticmethod
    def clearDirectoryAsRoot(*args):
        image = Docker.DockerImage("ubuntu:16.04")
        image.run(
            "rm -rf " + " ".join(["%s/*" % p for p in args]), 
            volumes={a:a for a in args}, 
            options="--rm"
            )

    def _run_command(self, command, log_filename, env, timeout, heartbeat, docker_image):
        tail_proc = None
        
        try:
            with open(log_filename, 'a') as build_log:
                if self.verbose:
                    def printer(l):
                        print l
                    tail_proc = SubprocessRunner.SubprocessRunner(["tail", "-f",log_filename], printer, printer)
                    tail_proc.start()

                print >> build_log, "********************************************"

                print >> build_log, "TestLooper Environment Variables:"
                for e in sorted(env):
                    print >> build_log, "\t%s=%s" % (e, env[e])
                print >> build_log

                if docker_image is not None:
                    print >> build_log, "DockerImage is ", docker_image.image
                build_log.flush()

                print >> build_log, "Working Directory: /test_looper/src"
                build_log.flush()

                print >> build_log, "TestLooper Running command ", command
                build_log.flush()

                print >> build_log, "********************************************"
                print >> build_log

            logging.info("Running command: '%s'. Log: %s. Docker Image: %s", 
                command, 
                log_filename,
                docker_image.image if docker_image is not None else "<none>"
                )

            assert docker_image is not None

            with DockerWatcher.DockerWatcher(self.name_prefix) as watcher:
                assert log_filename.startswith(self.directories.test_output_dir)

                log_filename_in_container = os.path.join("/test_looper/output", os.path.relpath(log_filename, self.directories.test_output_dir))

                container = watcher.run(
                    docker_image,
                    ["/bin/bash", "-c", command, ">", log_filename_in_container, "2>&1"],
                    volumes={
                        self.directories.scratch_dir: "/test_looper/scratch",
                        self.directories.test_inputs_dir: "/test_looper/test_inputs",
                        self.directories.repo_copy_dir: "/test_looper/src",
                        self.directories.test_output_dir: "/test_looper/output",
                        self.directories.build_output_dir: "/test_looper/build_output",
                        self.directories.ccache_dir: "/test_looper/ccache"
                        },
                    privileged=True,
                    shm_size="1G",
                    environment=env,
                    working_dir="/test_looper/src"
                    )

                t0 = time.time()
                ret_code = None
                extra_message = None
                while ret_code is None:
                    try:
                        ret_code = container.wait(timeout=self.heartbeatInterval)
                    except requests.exceptions.ReadTimeout:
                        heartbeat()
                        if time.time() - t0 > timeout:
                            ret_code = 1
                            container.stop()
                            extra_message = "Test timed out, so we're stopping the test."
                    except requests.exceptions.ConnectionError:
                        heartbeat()
                        if time.time() - t0 > timeout:
                            ret_code = 1
                            container.stop()
                            extra_message = "Test timed out, so we're stopping the test."

                with open(log_filename, 'a') as build_log:
                    print >> build_log, container.logs()
                    print >> build_log
                    if extra_message:
                        print >> build_log, extra_message
                    print >> build_log, "Process exited with code ", ret_code
                    build_log.flush()
                    
            return ret_code == 0
        finally:
            if tail_proc is not None:
                tail_proc.stop()

    def resetToCommitInDir(self, repoName, commitHash, targetDir):
        git_repo = self.getRepoCacheByName(repoName)

        if not git_repo.isInitialized():
            git_repo.cloneFrom(self.source_control.getRepo(repoName).cloneUrl())

        try:
            git_repo.resetToCommitInDirectory(commitHash, targetDir)
            os.unlink(os.path.join(targetDir, ".git"))
        except:
            logging.error(traceback.format_exc())
            return False

        return True

    def resetToCommit(self, repoName, commitHash):
        #check out a working copy
        self.clearDirectoryAsRoot(self.directories.repo_copy_dir)
        shutil.rmtree(self.directories.repo_copy_dir)

        return self.resetToCommitInDir(repoName, commitHash, self.directories.repo_copy_dir)

    @staticmethod
    def ensureDirectoryExists(path):
        if os.path.exists(path):
            return
        try:
            os.makedirs(path)
        except os.error as e:
            if e.errno != errno.EEXIST:
                raise

    def createNextTestDirForCommit(self, repoName, commitHash):
        revisionDir = os.path.join(self.directories.test_data_dir, repoName, commitHash)

        self.ensureDirectoryExists(revisionDir)

        iters = os.listdir(revisionDir)
        curIter = len(iters)

        testOutputDir = os.path.join(revisionDir, str(curIter))
        self.ensureDirectoryExists(testOutputDir)
        return testOutputDir

    @staticmethod
    def extractPerformanceTests(outPerformanceTestsFile, testName):
        if os.path.exists(outPerformanceTestsFile):
            performanceTestsList = []

            #verify that we can dump this as json. If we fail, we'll still be able to understand
            #what happened
            simplejson.dumps(performanceTestsList)

            return performanceTestsList
        else:
            return []

    def _purge_build_cache(self):
        self.ensureDirectoryExists(self.directories.build_cache_dir)
        
        while self._is_build_cache_full():
            self._remove_oldest_cached_build()

    def _is_build_cache_full(self):
        cache_count = len(os.listdir(self.directories.build_cache_dir))

        logging.info("Checking the build cache: there are %s items in it", cache_count)

        return cache_count >= self.max_build_cache_depth

    def _remove_oldest_cached_build(self):
        def full_path(p):
            return os.path.join(self.directories.build_cache_dir, p)
        cached_builds = sorted([(os.path.getctime(full_path(p)), full_path(p))
                                for p in os.listdir(self.directories.build_cache_dir)])
        shutil.rmtree(cached_builds[0][1])

    @staticmethod
    def getDockerImageFromRepo(git_repo, commitHash, pathToDockerfile):
        source = git_repo.getFileContents(commitHash, pathToDockerfile)
        if source is None:
            raise Exception("No file found at %s in commit %s" % (pathToDockerfile, commitHash))

        return Docker.DockerImage.from_dockerfile_as_string(None, source, create_missing=True)

    def resolveEnvironment(self, environment):
        seen = set([environment])
        while environment.matches.Import:
            environment = self.environmentDefinitionFor(environment.repo, environment.commitHash, environment.name)
            assert environment not in seen, "Circular environment definitions found"
            seen.add(environment)
            
        return environment

    def getDockerImage(self, testEnvironment, output_dir):
        assert testEnvironment.matches.Environment
        assert testEnvironment.platform.matches.linux
        assert testEnvironment.image.matches.Dockerfile

        repoName = testEnvironment.image.repo
        commitHash = testEnvironment.image.commitHash
        pathToDockerfile = testEnvironment.image.dockerfile

        git_repo = self.getRepoCacheByName(repoName)

        try:
            return self.getDockerImageFromRepo(git_repo, commitHash, pathToDockerfile)
        except Exception as e:
            logging.error("Failed to produce docker image:\n%s", traceback.format_exc())
            self.ensureDirectoryExists(output_dir)
            with open(os.path.join(output_dir,"docker_configuration_error.log"),"w") as f:
                print >> f, "Failed to get a docker image configured by %s:\n\n%s" % (
                    pathToDockerfile,
                    traceback.format_exc()
                    )

        return None

    def create_test_result(self, is_success, testId, repoName, commitHash, message=None):
        result = TestResult.TestResultOnMachine(is_success,
                                                testId,
                                                repoName, 
                                                commitHash,
                                                [], [],
                                                self.machineInfo.machineId,
                                                time.time()
                                                )
        if message:
            if not is_success:
                logging.error('Producing failure result for %s on %s/%s: %s', testId, repoName, commitHash, message)
            result.recordLogMessage(message)
        return result

    def testAndEnvironmentDefinitionFor(self, repoName, commitHash):
        path = self.getRepoCacheByName(repoName).getTestDefinitionsPath(commitHash)

        assert path is not None

        testText = self.getRepoCacheByName(repoName).getFileContents(commitHash, path)

        return TestDefinitionScript.extract_tests_from_str(repoName, commitHash, os.path.splitext(path)[1], testText)

    def environmentDefinitionFor(self, repoName, commitHash, envName):
        return self.testAndEnvironmentDefinitionFor(repoName, commitHash)[1].get(envName)

    def testDefinitionFor(self, repoName, commitHash, testName):
        return self.testAndEnvironmentDefinitionFor(repoName, commitHash)[0].get(testName)

    def runTest(self, testId, repoName, commitHash, testName, heartbeat):
        """Run a test (given by name) on a given commit and return a TestResultOnMachine"""
        self.cleanup()

        if not self.resetToCommit(repoName, commitHash):
            return self.create_test_result(False, testId, repoName, commitHash, "Failed to checkout code")

        try:
            testDefinition = self.testDefinitionFor(repoName, commitHash, testName)

            if not testDefinition:
                return self.create_test_result(False, testId, repoName, commitHash, "No test named " + testName)
            
            if testDefinition.matches.Build and self.artifactStorage.build_exists(self.artifactKeyForTest(repoName, commitHash, testName)):
                return self.create_test_result(True, testId, repoName, commitHash)
            
            return self._run_task(testId, repoName, commitHash, testDefinition, heartbeat)

        except TestLooperClient.ProtocolMismatchException:
            raise
        except:
            error_message = "Test failed because of exception: %s" % traceback.format_exc()
            logging.error(error_message)
            return self.create_test_result(False, testId, repoName, commitHash, error_message)

    def extract_package(self, package_file, target_dir):
        with tarfile.open(package_file) as tar:
            root = tar.next()
            if root is None:
                raise Exception("Package %s is empty" % package_file)
            logging.info("Extracting package %s to %s", package_file, target_dir)
            tar.extractall(target_dir)

    def grabDependency(self, expose_as, dep, repoName, commitHash):
        target_dir = os.path.join(self.directories.test_inputs_dir, expose_as)

        if dep.matches.InternalBuild or dep.matches.ExternalBuild:
            if dep.matches.ExternalBuild:
                repoName, commitHash = dep.repo, dep.commitHash

            if not self.artifactStorage.build_exists(self.artifactKeyForTest(repoName, commitHash, dep.name + "/" + dep.environment)):
                return "can't run tests because dependent external build %s doesn't exist" % (repoName + "/" + commitHash + "/" + dep.name + "/" + dep.environment)

            path = self._download_build(repoName, commitHash, dep.name + "/" + dep.environment)
            
            self.ensureDirectoryExists(target_dir)
            self.extract_package(path, target_dir)
            return None

        if dep.matches.Source:
            self.resetToCommitInDir(dep.repo, dep.commitHash, target_dir)
            return None

        return "Unknown dependency type: %s" % dep

    def _run_task(self, testId, repoName, commitHash, test_definition, heartbeat):
        environment = self.resolveEnvironment(test_definition.environment)

        all_dependencies = {}
        all_dependencies.update(environment.dependencies)
        all_dependencies.update(test_definition.dependencies)

        for expose_as, dep in all_dependencies.iteritems():
            errStringOrNone = self.grabDependency(expose_as, dep, repoName, commitHash)

            if errStringOrNone is not None:
                return self.create_test_result(False, testId, repoName, commitHash, errStringOrNone)

        if test_definition.matches.Build:
            command = test_definition.buildCommand
        else:
            command = test_definition.testCommand

        image = self.getDockerImage(environment, self.directories.test_output_dir)

        env_overrides = self.environment_variables(testId, repoName, commitHash, test_definition.matches.Build)
        
        logging.info("Machine %s is starting run for %s %s. Command: %s",
                     self.machineInfo.machineId,
                     repoName, 
                     commitHash,
                     command)

        is_success = self.runTestUsingScript(command,
                                             env_overrides,
                                             heartbeat,
                                             docker_image=image
                                             )

        if is_success and test_definition.matches.Build:
            if not self._upload_build(repoName, commitHash, test_definition.name):
                logging.error('Failed to upload build for %s/%s', repoName, commitHash, test_definition.name)
                is_success = False

        test_result = self.create_test_result(is_success, testId, repoName, commitHash)

        heartbeat()
        
        if not is_success or test_definition.matches.Build:
            logging.info("machine %s uploading artifacts for test %s", self.machineInfo.machineId, testId)

            self.artifactStorage.uploadTestArtifacts(
                testId,
                self.machineInfo.machineId,
                self.directories.test_output_dir
                )

        return test_result
    
    def artifactKeyForTest(self, repoName, commitHash, testName):
        return (repoName + "/" + commitHash + "/" + testName).replace("/", "_") + ".tar"

    def _upload_build(self, repoName, commitHash, testName):
        #upload all the data in our directory
        tarball_name = os.path.join(
            self.directories.build_cache_dir, 
            self.artifactKeyForTest(repoName, commitHash, testName)
            )

        if not os.path.exists(tarball_name):
            logging.info("Tarballing %s into %s", self.directories.build_output_dir, tarball_name)
            SubprocessRunner.callAndAssertSuccess(
                ["tar", "cvf", tarball_name, "--directory", self.directories.build_output_dir, "."
                ])
            logging.info("Resulting tarball at %s is %.2f MB", tarball_name, os.stat(tarball_name).st_size / 1024.0**2)
        else:
            logging.warn("A build for %s/%s/%s already exists at %s", repoName, commitHash, testName, tarball_name)

        try:
            logging.info("Uploading %s to %s", tarball_name, self.artifactKeyForTest(repoName, commitHash, testName))

            self.artifactStorage.upload_build(self.artifactKeyForTest(repoName, commitHash, testName), tarball_name)
            return True
        except:
            logging.error("Failed to upload package '%s' to %s/%s\n%s",
                          tarball_name,
                          repoName, 
                          commitHash,
                          traceback.format_exc()
                          )
            return False

    def _download_build(self, repoName, commitHash, testName):
        path = os.path.join(self.directories.build_cache_dir, self.artifactKeyForTest(repoName, commitHash, testName))
        
        if not os.path.exists(path):
            logging.info("Downloading build for %s/%s to %s", repoName, commitHash, testName, path)
            self.artifactStorage.download_build(self.artifactKeyForTest(repoName, commitHash, testName), path)

        return path

    def environment_variables(self, testId, repoName, commitHash, isBuild):
        return  {
            'TEST_REPO': repoName,
            'REVISION': commitHash,
            'TEST_SRC_DIR': "/test_looper/src",
            'TEST_INPUTS': "/test_looper/test_inputs",
            'TEST_SCRATCH_DIR': "/test_looper/scratch",
            'TEST_OUTPUT_DIR': "/test_looper/output",
            'TEST_BUILD_OUTPUT_DIR': "/test_looper/build_output",
            'TEST_CCACHE_DIR': "/test_looper/ccache",
            'TEST_LOOPER_TEST_ID': testId
            }

    def runTestUsingScript(self, script, env_overrides, heartbeat, docker_image):
        test_logfile = os.path.join(self.directories.test_output_dir, 'test_out.log')

        logging.info("Machine %s is logging to %s with",
                     self.machineInfo.machineId,
                     test_logfile)

        return self._run_command(
            script,
            test_logfile,
            env_overrides,
            self.timeout,
            heartbeat,
            docker_image=docker_image
            )
