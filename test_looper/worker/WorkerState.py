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
import test_looper.core.TestScriptDefinition as TestScriptDefinition
import test_looper.core.TestResult as TestResult
import test_looper

class TestLooperDirectories:
    def __init__(self, worker_directory):
        self.repo_dir = os.path.join(worker_directory, "repo")
        self.repo_copy_dir = os.path.join(worker_directory, "repo_copy")
        self.build_dir = os.path.join(worker_directory, "build")
        self.output_dir = os.path.join(worker_directory, "output")
        self.test_data_dir = os.path.join(worker_directory, "test_data")
        self.build_cache_dir = os.path.join(worker_directory, "build_cache")
        self.ccache_dir = os.path.join(worker_directory, "ccache")

    def all(self):
        return [self.repo_dir, self.repo_copy_dir, self.build_dir, self.test_data_dir, 
                self.build_cache_dir, self.ccache_dir, self.output_dir]

class WorkerState(object):
    def __init__(self, worker_directory, source_control, artifactStorage, machineInfo, timeout=900, verbose=False):
        import test_looper.worker.TestLooperWorker

        assert isinstance(worker_directory, str)

        self.verbose = verbose

        self.timeout = timeout

        self.directories = TestLooperDirectories(worker_directory)

        self.machineInfo = machineInfo

        for path in self.directories.all():
            self.ensureDirectoryExists(path)

        self.max_build_cache_depth = 10
        self.heartbeatInterval = TestLooperClient.TestLooperClient.HEARTBEAT_INTERVAL

        self.source_control = source_control

        self.artifactStorage = artifactStorage

        self.git_repo = Git.Git(self.directories.repo_dir)

        self.initializeGitRepo()

        self.cleanup()

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

    def initializeGitRepo(self):
        if not self.git_repo.isInitialized():
            self.git_repo.cloneFrom(self.source_control.cloneUrl())

    def cleanup(self):
        Docker.DockerImage.removeDanglingDockerImages()
        self.clearDirectoryAsDocker(
            self.directories.test_data_dir, 
            self.directories.output_dir, 
            self.directories.build_dir, 
            self.directories.repo_copy_dir
            )
        
    @staticmethod
    def clearDirectoryAsDocker(*args):
        image = Docker.DockerImage("ubuntu:16.04")
        image.run(
            "rm -rf " + " ".join(["%s/*" % p for p in args]), 
            volumes={a:a for a in args}, 
            options="--rm"
            )

    def _run_command(self, command, log_filename, env, timeout, heartbeat, docker_image):
        with open(log_filename, 'a') as build_log:
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

            with DockerWatcher.DockerWatcher() as watcher:
                container = watcher.run(
                    docker_image,
                    ["/bin/bash", "-c", command],
                    volumes={
                        self.directories.build_dir: "/test_looper/build",
                        self.directories.repo_copy_dir: "/test_looper/src",
                        self.directories.output_dir: "/test_looper/output",
                        self.directories.ccache_dir: "/test_looper/ccache"
                        },
                    environment=env,
                    working_dir="/test_looper/src"
                    )

                t0 = time.time()
                ret_code = None
                while ret_code is None:
                    try:
                        ret_code = container.wait(timeout=self.heartbeatInterval)
                    except requests.exceptions.ReadTimeout:
                        heartbeat()
                        if time.time() - t0 > timeout:
                            ret_code = 1
                            container.stop()

                print >> build_log, container.logs()

        if self.verbose:
            with open(log_filename, 'r') as f:
                print f.read()

        return ret_code == 0

    def resetToCommit(self, revision):
        if not self.git_repo.resetToCommit(revision):
            return False

        #make a copy of the git_repo in the working directory, minus the .git directory
        for p in os.listdir(self.directories.repo_dir):
            if p != ".git":
                if SubprocessRunner.callAndReturnResultWithoutOutput(
                        ["cp", "-r", os.path.join(self.directories.repo_dir, p), self.directories.repo_copy_dir]
                        ) != 0:
                    logging.error("Failed to copy %s into the repo_copy_dir", p)
                    return False

        return True

    @staticmethod
    def ensureDirectoryExists(path):
        try:
            os.makedirs(path)
        except os.error as e:
            if e.errno != errno.EEXIST:
                raise

    def createNextTestDirForCommit(self, commitId):
        revisionDir = os.path.join(self.directories.test_data_dir, commitId)

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

    def _build(self, testId, commitId, build_command, heartbeat, docker_image):
        build_log = os.path.join(self.directories.output_dir, 'build.log')

        build_env = self.environment_variables(testId, commitId)

        if not self.resetToCommit(commitId):
            return False

        return self._run_command(build_command, build_log, build_env, self.timeout, heartbeat, docker_image)

    def _purge_build_cache(self):
        self.ensureDirectoryExists(self.directories.build_cache_dir)
        
        while self._is_build_cache_full():
            self._remove_oldest_cached_build()

    def _is_build_cache_full(self):
        return len(os.listdir(self.directories.build_cache_dir)) >= self.max_build_cache_depth

    def _remove_oldest_cached_build(self):
        def full_path(p):
            return os.path.join(self.directories.build_cache_dir, p)
        cached_builds = sorted([(os.path.getctime(full_path(p)), full_path(p))
                                for p in os.listdir(self.directories.build_cache_dir)])
        shutil.rmtree(cached_builds[0][1])

    def getDockerImage(self, commitId, dockerConf, output_dir):
        try:
            if 'tag' in dockerConf:
                tagname = dockerConf['tag']

                for char in tagname:
                    if not (char.isalnum() or char in ".-_:"):
                        raise Exception("Invalid tag name: " + tagname)

                image_name = dockerConf["tag"]
                
                d = Docker.DockerImage(image_name)

                if not d.pull():
                    raise Exception("Couldn't find docker explicitly named image %s" % d.image)

                return d

            if 'dockerfile' in dockerConf:
                source = self.source_control.source_repo.getFileContents(commitId, dockerConf["dockerfile"])
                if source is None:
                    raise Exception("No file found at %s in commit %s" % (dockerConf["dockerfile"], commitId))

                return Docker.DockerImage.from_dockerfile_as_string(None, source, create_missing=True)

            raise Exception("No docker configuration was provided. Test should define one of " + 
                    "'tag', or 'dockerfile'"
                    )
        except Exception as e:
            logging.error("Failed to produce docker image:\n%s", traceback.format_exc())
            self.ensureDirectoryExists(output_dir)
            with open(os.path.join(output_dir,"docker_configuration_error.log"),"w") as f:
                print >> f, "Failed to get a docker image configured by %s:\n\n%s" % (
                    dockerConf,
                    traceback.format_exc()
                    )

        return None

    def create_test_result(self, is_success, testId, commitId, message=None):
        result = TestResult.TestResultOnMachine(is_success,
                                                testId,
                                                commitId,
                                                [], [],
                                                self.machineInfo.machineId,
                                                time.time()
                                                )
        if message:
            result.recordLogMessage(message)
        return result

    def dockerImageFor(self, commitId, testName):
        self.git_repo.resetToCommit(commitId)
        
        defs = self.testDefinitionFor(commitId, testName)[0]

        return self.getDockerImage(
            commitId, 
            defs.docker, 
            self.directories.output_dir
            )

    def testDefinitionFor(self, commitId, testName):
        json = simplejson.loads(self.source_control.getTestScriptDefinitionsForCommit(commitId))

        testScriptDefinitions = [x for x in 
            TestScriptDefinition.TestScriptDefinition.testSetFromJson(json)
                if x.testName == testName
            ]

        return testScriptDefinitions


    def runTest(self, testId, commitId, testName, heartbeat):
        """Run a test (given by name) on a given commit and return a TestResultOnMachine"""
        self.cleanup()

        if not self.resetToCommit(commitId):
            return self.create_test_result(False, testId, commitId, "Failed to checkout code")

        try:
            json = simplejson.loads(self.source_control.getTestScriptDefinitionsForCommit(commitId))

            testScriptDefinitions = [x for x in 
                TestScriptDefinition.TestScriptDefinition.testSetFromJson(json)
                    if x.testName == testName
                ]
            if not testScriptDefinitions:
                return self.create_test_result(False, testId, commitId, "No test named " + testName)
            if len(testScriptDefinitions) > 1:
                return self.create_test_result(False, testId, commitId, "Multiple tests named " + testName)
            testScriptDefinition = testScriptDefinitions[0]
                            
            if testName == 'build':
                result = self._run_build_task(testId, commitId, testScriptDefinition, heartbeat)
            else:
                result = self._run_test_task(testId, commitId, testScriptDefinition, heartbeat)

        except TestLooperClient.ProtocolMismatchException:
            raise
        except:
            error_message = "Test failed because of exception: %s" % traceback.format_exc()
            logging.error(error_message)
            result = self.create_test_result(False, testId, commitId, error_message)

        logging.info("Machine %s publishing test results: %s",
                     self.machineInfo.machineId,
                     result)

        return result

    def _run_build_task(self, testId, commitId, test_definition, heartbeat):
        build_command = test_definition.testCommand
        
        if self.artifactStorage.build_exists(commitId):
            return self.create_test_result(True, testId, commitId)

        image = self.getDockerImage(
            commitId, 
            test_definition.docker, 
            self.directories.output_dir
            )
        
        if (    not image or
                not self._build(testId, commitId, build_command, heartbeat, image) or
                not self._upload_build(commitId)
                ):
            is_success = False
        else:
            is_success = True

        if not is_success:
            logging.error("Failed to build commit: %s", commitId)

            self.artifactStorage.uploadTestArtifacts(
                testId, 
                self.machineInfo.machineId, 
                self.directories.output_dir
                )

        return self.create_test_result(is_success, testId, commitId)


    @staticmethod
    def extract_package(package_file, target_dir):
        with tarfile.open(package_file) as tar:
            root = tar.next()
            if root is None:
                raise Exception("Package %s is empty" % package_file)
            package_dir = os.path.join(target_dir, "build")
            logging.info("Extracting package to %s", package_dir)
            tar.extractall(package_dir)
            return package_dir


    def _run_test_task(self, testId, commitId, test_definition, heartbeat):
        if not self.artifactStorage.build_exists(commitId):
            return self.create_test_result(False, testId, commitId, "can't run tests because the build doesn't exist")

        command = test_definition.testCommand
        
        path = self._download_build(commitId)

        self.extract_package(path, self.directories.build_dir)

        image = self.getDockerImage(commitId, test_definition.docker, self.directories.output_dir)

        env_overrides = self.environment_variables(testId, commitId)
        
        logging.info("Machine %s is starting run for %s. Command: %s",
                     self.machineInfo.machineId,
                     commitId,
                     command)

        is_success = self.runTestUsingScript(command,
                                             env_overrides,
                                             heartbeat,
                                             docker_image=image
                                             )

        test_result = self.create_test_result(is_success, testId, commitId)

        if 0:
            self.capture_perf_results(test_definition,
                                      os.path.join(test_output_dir, self.perf_test_output_file),
                                      test_result)

        if not is_success:
            heartbeat()
            logging.info("machine %s uploading artifacts", self.machineInfo.machineId)
            self.artifactStorage.uploadTestArtifacts(
                testId,
                self.machineInfo.machineId,
                self.directories.output_dir
                )

        return test_result

    def _upload_build(self, commitId):
        #upload all the data in our directory
        tarball_name = os.path.join(
            self.directories.build_cache_dir, 
            commitId + ".tar"
            )

        if not os.path.exists(tarball_name):
            SubprocessRunner.callAndAssertSuccess(
                ["tar", "cvf", tarball_name, self.directories.output_dir
                ])

        try:
            self.artifactStorage.upload_build(commitId, tarball_name)
            return True
        except:
            logging.error("Failed to upload package '%s' to %s\n%s",
                          tarball_name,
                          commitId,
                          traceback.format_exc()
                          )
            return False

    def _find_cached_build(self, commitId):
        build_path = os.path.join(self.directories.build_cache_dir, commitId + ".tar")
        if os.path.exists(build_path):
            return build_path

    
    def _download_build(self, commitId):
        path = os.path.join(self.directories.build_cache_dir, commitId + ".tar")
        if not os.path.exists(path):
            self.artifactStorage.download_build(commitId, path)
        return path

    def environment_variables(self, testId, commitId):
        return  {
            'REVISION': commitId,
            'REPO_DIR': "/test_looper/src",
            'BUILD_DIR': "/test_looper/build",
            'OUTPUT_DIR': "/test_looper/output",
            'CCACHE_DIR': "/test_looper/ccache",
            'TEST_LOOPER_TEST_ID': testId
            }

    def runTestUsingScript(self, script, env_overrides, heartbeat, docker_image):
        test_logfile = os.path.join(self.directories.output_dir, 'test_out.log')
        logging.info("Machine %s is logging to %s with",
                     self.machineInfo.machineId,
                     test_logfile)
        success = False
        try:
            success = self._run_command(
                script,
                test_logfile,
                env_overrides,
                self.timeout,
                heartbeat,
                docker_image=docker_image
                )
        except test_looper.worker.TestLooperWorker.TestInterruptException:
            logging.info("TestInterruptException in machine: %s. Heartbeat response: %s",
                         self.machineInfo.machineId,
                         self.heartbeatResponse)
            if self.stopEvent.is_set():
                return
            success = self.heartbeatResponse == TestResult.TestResult.HEARTBEAT_RESPONSE_DONE
        except:
            import traceback
            logging.error(traceback.format_exc())
            return False

        return success


    def capture_perf_results(self, test_name, perf_output_file, test_result):
        try:
            test_result.recordPerformanceTests(
                self.extractPerformanceTests(perf_output_file,
                                                                     test_name)
                )
        except:
            logging.error(
                "Machine %s failed to read performance test data: %s",
                self.machineInfo.machineId,
                traceback.format_exc()
                )
            test_result.recordLogMessage("Failed to read performance tests")
