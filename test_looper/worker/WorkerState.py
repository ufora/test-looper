import collections
import errno
import json
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
import subprocess
import base64
import tempfile
import cStringIO as StringIO

for name in ["boto3", "requests", "urllib"]:
    logging.getLogger(name).setLevel(logging.CRITICAL)

import test_looper.core.SubprocessRunner as SubprocessRunner
import test_looper.core.tools.Git as Git
import test_looper.core.DirectoryScope as DirectoryScope

if sys.platform != "win32":
    import docker
    import test_looper.core.tools.Docker as Docker
    import test_looper.core.tools.DockerWatcher as DockerWatcher
else:
    docker = None
    Docker = None
    DockerWatcher = None

import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper

class DummyWorkerCallbacks:
    def __init__(self, localTerminal=False):
        self.logMessages = []
        self.localTerminal = localTerminal

    def heartbeat(self, logMessage=None):
        if logMessage is not None:
            self.logMessages.append(logMessage)

    def terminalOutput(self, output):
        pass

    def subscribeToTerminalInput(self, callback):
        pass

HEARTBEAT_INTERVAL=3

class NAKED_MACHINE:
    pass

class TestLooperDirectories:
    def __init__(self, worker_directory):
        self.repo_cache = os.path.join(worker_directory, "repos")
        self.repo_copy_dir = os.path.join(worker_directory, "src")
        self.scratch_dir = os.path.join(worker_directory, "scratch_dir")
        self.command_dir = os.path.join(worker_directory, "command")
        self.test_inputs_dir = os.path.join(worker_directory, "test_inputs")
        self.test_output_dir = os.path.join(worker_directory, "test_output")
        self.build_output_dir = os.path.join(worker_directory, "build_output")
        self.test_data_dir = os.path.join(worker_directory, "test_data")
        self.build_cache_dir = os.path.join(worker_directory, "build_cache")
        self.ccache_dir = os.path.join(worker_directory, "ccache")

    def all(self):
        return [self.repo_copy_dir, self.scratch_dir, self.command_dir, self.test_inputs_dir, self.test_data_dir, 
                self.build_cache_dir, self.ccache_dir, self.test_output_dir, self.build_output_dir, self.repo_cache]

class TestDefinitionResolver:
    def __init__(self, git_repo_lookup):
        self.git_repo_lookup = git_repo_lookup

    def resolveEnvironment(self, environment):
        if environment.matches.Environment:
            return environment

        dependencies = {}

        def import_dep(dep):
            """Grab a dependency and all its children and stash them in 'dependencies'"""
            if dep in dependencies:
                return

            underlying_env = self.environmentDefinitionFor(dep.repo, dep.commitHash, dep.name)

            assert underlying_env is not None

            dependencies[dep] = underlying_env

            if underlying_env.matches.Import:
                for dep in underlying_env.imports:
                    import_dep(dep)

        for dep in environment.imports:
            import_dep(dep)

        return TestDefinition.merge_environments(environment, dependencies)

    def environmentDefinitionFor(self, repoName, commitHash, envName):
        return self.testAndEnvironmentDefinitionFor(repoName, commitHash)[1].get(envName)

    def testAndEnvironmentDefinitionFor(self, repoName, commitHash):
        path = self.git_repo_lookup(repoName).getTestDefinitionsPath(commitHash)

        if path is None:
            return {}, {}, {}

        testText = self.git_repo_lookup(repoName).getFileContents(commitHash, path)

        return TestDefinitionScript.extract_tests_from_str(repoName, commitHash, os.path.splitext(path)[1], testText)



class WorkerState(object):
    def __init__(self, name_prefix, worker_directory, source_control, artifactStorage, machineId, hardwareConfig, verbose=False, resolver=None):
        import test_looper.worker.TestLooperWorker

        self.name_prefix = name_prefix

        assert isinstance(worker_directory, (str,unicode)), worker_directory
        worker_directory = str(worker_directory)

        self.worker_directory = worker_directory

        self.verbose = verbose

        self.directories = TestLooperDirectories(worker_directory)

        self.repos_by_name = {}

        self.machineId = machineId

        self.hardwareConfig = hardwareConfig

        for path in self.directories.all():
            self.ensureDirectoryExists(path)

        self.max_build_cache_depth = 10

        self.artifactStorage = artifactStorage

        self.source_control = source_control

        self.resolver = resolver or TestDefinitionResolver(self.getRepoCacheByName)

        self.cleanup()

    def callHeartbeatInBackground(self, log_function, logMessage=None):
        if logMessage is not None:
            log_function(time.asctime() + " TestLooper> " + logMessage + "\n")

        stop = threading.Event()
        receivedException = [None]

        def heartbeatThread():
            while not stop.is_set():
                stop.wait(10)
                if stop.is_set():
                    return
                else:
                    try:
                        log_function("")
                    except Exception as e:
                        stop.set()
                        receivedException[0] = e

        loggingThread = threading.Thread(target=heartbeatThread)

        class Scope:
            def __enter__(scope):
                loggingThread.start()

            def __exit__(scope, exc_type, exc_value, traceback):
                stop.set()
                loggingThread.join()

                if receivedException[0] is not None:
                    if exc_value is not None:
                        logging.error("Got exception %s but also got a heartbeat exception." % exc_value)
                    raise receivedException[0]

        return Scope()
    
    def getRepoCacheByName(self, name):
        if name not in self.repos_by_name:
            self.repos_by_name[name] = Git.Git(str(os.path.join(self.directories.repo_cache, name)))

            if not self.repos_by_name[name].isInitialized():
                self.repos_by_name[name].cloneFrom(self.source_control.getRepo(name).cloneUrl())

        return self.repos_by_name[name]

    def cleanup(self):
        if Docker is not None:
            Docker.DockerImage.removeDanglingDockerImages()

        self.clearDirectoryAsRoot(
            self.directories.test_data_dir, 
            self.directories.test_output_dir,
            self.directories.build_output_dir,
            self.directories.scratch_dir, 
            self.directories.test_inputs_dir, 
            self.directories.command_dir,
            self.directories.repo_copy_dir
            )

    def wants_to_run_cleanup(self):
        return True

    def clearDirectoryAsRoot(self, *args):
        if Docker:
            image = Docker.DockerImage("ubuntu:16.04")
            image.run(
                "rm -rf " + " ".join(["%s/*" % p for p in args]), 
                volumes={a:a for a in args}, 
                options="--rm"
                )
        else:
            for a in args:
                try:
                    self.ensureDirectoryExists(a)
                    shutil.rmtree(a)
                    self.ensureDirectoryExists(a)
                except:
                    logging.error("Failure clearing directory %s:\n%s", a, traceback.format_exc())

    def volumesToExpose(self):
        return {
            self.directories.scratch_dir: "/test_looper/scratch",
            self.directories.test_inputs_dir: "/test_looper/test_inputs",
            self.directories.repo_copy_dir: "/test_looper/src",
            self.directories.test_output_dir: "/test_looper/output",
            self.directories.build_output_dir: "/test_looper/build_output",
            self.directories.ccache_dir: "/test_looper/ccache",
            self.directories.command_dir: "/test_looper/command"
            }

    def _run_deployment(self, command, env, workerCallback, docker_image):
        build_log = StringIO.StringIO()

        self.dumpPreambleLog(build_log, env, docker_image, command)

        workerCallback.terminalOutput(build_log.getvalue().replace("\n","\r\n"))

        logging.info("Running command: '%s' on %s", 
            command, 
            "Docker image: " + docker_image.image if docker_image is not NAKED_MACHINE else "the naked machine"
            )

        if sys.platform == "win32":
            assert docker_image is NAKED_MACHINE

            env_to_pass = dict(os.environ)
            env_to_pass.update(env)

            invoker_path = os.path.join(self.directories.command_dir,"command_invoker.ps1")
            command_path = os.path.join(self.directories.command_dir,"command.ps1")
            with open(command_path,"w") as cmd_file:
                print >> cmd_file, "cd '" + self.directories.repo_copy_dir + "'"
                print >> cmd_file, "echo 'Welcome to TestLooper on Windows. Here is the current environment:'"
                print >> cmd_file, "gci env:* | sort-object name"
                print >> cmd_file, "echo '********************************'"
                print >> cmd_file, command

            with open(invoker_path,"w") as cmd_file:
                print >> cmd_file, "powershell.exe " + command_path
                print >> cmd_file, "powershell.exe"

            running_subprocess = subprocess.Popen(
                ["powershell.exe", "-ExecutionPolicy", "Bypass", invoker_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
                env=env_to_pass,
                creationflags=subprocess.CREATE_NEW_CONSOLE
                )

            logging.info("Powershell process has pid %s", running_subprocess.pid)
            
            time.sleep(.5)

            readthreadStop = threading.Event()
            def readloop(file):
                try:
                    while not readthreadStop.is_set():
                        data = os.read(file.fileno(), 4096)
                        if not data:
                            #do a little throttling
                            time.sleep(0.01)
                        else:
                            workerCallback.terminalOutput(data.replace("\n","\n\r"))
                except:
                    logging.error("Read loop failed:\n%s", traceback.format_exc())

            readthreads = [threading.Thread(target=readloop, args=(x,)) for x in [running_subprocess.stdout, running_subprocess.stderr]]
            for t in readthreads:
                t.daemon=True
                t.start()

            try:
                writeFailed = [False]
                def write(msg):
                    if not msg:
                        return
                    try:
                        if not writeFailed[0]:
                            if isinstance(msg, str):
                                running_subprocess.stdin.write(msg)
                            elif msg.matches.KeyboardInput:
                                running_subprocess.stdin.write(msg.bytes)
                    except:
                        writeFailed[0] = True 
                        logging.error("Failed to write to stdin: %s", traceback.format_exc())

                workerCallback.subscribeToTerminalInput(write)

                ret_code = None
                while ret_code is None:
                    try:
                        ret_code = running_subprocess.poll()
                        time.sleep(HEARTBEAT_INTERVAL)
                    except requests.exceptions.ReadTimeout:
                        pass
                    except requests.exceptions.ConnectionError:
                        pass

                    workerCallback.heartbeat()
            finally:
                try:
                    if ret_code is not None:
                        running_subprocess.terminate()
                except:
                    logging.info("Failed to terminate subprocess: %s", traceback.format_exc())
                readthreadStop.set()
        else:
            with open(os.path.join(self.directories.command_dir, "cmd.sh"), "w") as f:
                print >> f, command

            with open(os.path.join(self.directories.command_dir, "cmd_invoker.sh"), "w") as f:
                print >> f, "hostname testlooperworker"
                print >> f, "bash /test_looper/command/cmd.sh"
                print >> f, "export PS1='${debian_chroot:+($debian_chroot)}\\[\\033[01;32m\\]\\u@\\h\\[\\033[00m\\]:\\[\\033[01;34m\\]\\w\\[\\033[00m\\]\\$ '"
                print >> f, "bash --noprofile --norc"

            assert docker_image is not None

            env = dict(env)
            env["TERM"] = "xterm-256color"

            with DockerWatcher.DockerWatcher(self.name_prefix) as watcher:
                if isinstance(workerCallback, DummyWorkerCallbacks) and workerCallback.localTerminal:
                    container = watcher.run(
                        docker_image,
                        ["/bin/bash", "/test_looper/command/cmd_invoker.sh"],
                        volumes=self.volumesToExpose(),
                        privileged=True,
                        shm_size="1G",
                        environment=env,
                        working_dir="/test_looper/src",
                        tty=True,
                        stdin_open=True,
                        start=False
                        )
                    import dockerpty

                    client = docker.from_env()
                    client.__dict__["start"] = lambda c, *args, **kwds: client.api.start(c.id, *args, **kwds)
                    client.__dict__["inspect_container"] = lambda c: client.api.inspect_container(c.id)
                    client.__dict__["attach_socket"] = lambda c,*args,**kwds: client.api.attach_socket(c.id, *args, **kwds)
                    client.__dict__["resize"] = lambda c,*args,**kwds: client.api.resize(c.id, *args, **kwds)
                    dockerpty.start(client, container)
                else:
                    container = watcher.run(
                        docker_image,
                        ["/bin/bash", "/test_looper/command/cmd_invoker.sh"],
                        volumes=self.volumesToExpose(),
                        privileged=True,
                        shm_size="1G",
                        environment=env,
                        working_dir="/test_looper/src",
                        tty=True,
                        stdin_open=True
                        )

                    #these are standard socket objects connected to the container's TTY input/output
                    stdin = docker.from_env().api.attach_socket(container.id, params={'stdin':1,'stream':1,'logs':None})
                    stdout = docker.from_env().api.attach_socket(container.id, params={'stdout':1,'stream':1,'logs':None})

                    readthreadStop = threading.Event()
                    def readloop():
                        while not readthreadStop.is_set():
                            data = stdout.recv(4096)
                            if not data:
                                logging.info("Socket stdout connection to %s terminated", container.id)
                                return
                            workerCallback.terminalOutput(data)

                    readthread = threading.Thread(target=readloop)
                    readthread.start()

                    stdin.sendall("\n")

                    writeFailed = [False]
                    def write(msg):
                        if not msg:
                            return
                        try:
                            if not writeFailed[0]:
                                if isinstance(msg, str):
                                    stdin.sendall(msg)
                                elif msg.matches.KeyboardInput:
                                    stdin.sendall(msg.bytes)
                                elif msg.matches.Resize:
                                    logging.info("Terminal resizing to %s cols and %s rows", msg.cols, msg.rows)
                                    container.resize(msg.rows, msg.cols)
                        except:
                            writeFailed[0] = True 
                            logging.error("Failed to write to stdin: %s", traceback.format_exc())

                    workerCallback.subscribeToTerminalInput(write)
                    
                    try:
                        t0 = time.time()
                        ret_code = None
                        extra_message = None
                        while ret_code is None:
                            try:
                                ret_code = container.wait(timeout=HEARTBEAT_INTERVAL)
                            except requests.exceptions.ReadTimeout:
                                pass
                            except requests.exceptions.ConnectionError:
                                pass

                            workerCallback.heartbeat()
                    finally:
                        try:
                            container.remove(force=True)
                        except:
                            pass
                        readthreadStop.set()
                        readthread.join()
                        
    def dumpPreambleLog(self, build_log, env, docker_image, command):
        print >> build_log, "********************************************"

        print >> build_log, "TestLooper Environment Variables:"
        for e in sorted(env):
            print >> build_log, "\t%s=%s" % (e, env[e])
        print >> build_log

        if docker_image is not NAKED_MACHINE:
            print >> build_log, "DockerImage is ", docker_image.image
        build_log.flush()

        print >> build_log, "Working Directory: /test_looper/src"
        build_log.flush()

        print >> build_log, "TestLooper Running command:"
        print >> build_log, command
        build_log.flush()

        print >> build_log, "********************************************"
        print >> build_log
        build_log.flush()


    def _run_test_command(self, command, timeout, env, log_function, docker_image, dumpPreambleLog=True):
        if sys.platform == "win32":
            return self._run_test_command_windows(command, timeout, env, log_function, docker_image, dumpPreambleLog)
        else:
            return self._run_test_command_linux(command, timeout, env, log_function, docker_image, dumpPreambleLog)

    def _run_test_command_windows(self, command, timeout, env, log_function, docker_image, dumpPreambleLog):
        assert docker_image is NAKED_MACHINE

        env_to_pass = dict(os.environ)
        env_to_pass.update(env)

        t0 = time.time()

        command_path = os.path.join(self.directories.command_dir,"command.ps1")
        with open(command_path,"w") as cmd_file:
            print >> cmd_file, "cd '" + self.directories.repo_copy_dir + "'"
            if dumpPreambleLog:
                print >> cmd_file, "echo 'Welcome to TestLooper on Windows. Here is the current environment:'"
                print >> cmd_file, "gci env:* | sort-object name"
                print >> cmd_file, "echo '********************************'"
            print >> cmd_file, command

        running_subprocess = subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", command_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            env=env_to_pass,
            creationflags=subprocess.CREATE_NEW_CONSOLE
            )

        logging.info("Powershell process has pid %s", running_subprocess.pid)
        time.sleep(.5)

        readthreadStop = threading.Event()
        def readloop(file):
            try:
                while not readthreadStop.is_set():
                    data = os.read(file.fileno(), 4096)
                    if not data:
                        #do a little throttling
                        time.sleep(0.01)
                    else:
                        log_function(data)
            except:
                logging.error("Read loop failed:\n%s", traceback.format_exc())

        readthreads = [threading.Thread(target=readloop, args=(x,)) for x in [running_subprocess.stdout, running_subprocess.stderr]]
        for t in readthreads:
            t.daemon=True
            t.start()

        try:
            ret_code = None
            while ret_code is None:
                try:
                    ret_code = running_subprocess.poll()
                    time.sleep(HEARTBEAT_INTERVAL)
                except requests.exceptions.ReadTimeout:
                    pass
                except requests.exceptions.ConnectionError:
                    pass

                log_function("")

                if time.time() - t0 > timeout:
                    log_function("\n\n" + time.asctime() + " TestLooper> Process timed out (%s seconds).\n" % timeout)
                    running_subprocess.terminate()
                    return False
        finally:
            try:
                if ret_code is not None:
                    running_subprocess.terminate()
            except:
                logging.info("Failed to terminate subprocess: %s", traceback.format_exc())

            readthreadStop.set()
        
        log_function("\n\n" + time.asctime() + " TestLooper> Process exited with code %s\n" % ret_code)

        return ret_code == 0

    def _run_test_command_linux(self, command, timeout, env, log_function, docker_image, dumpPreambleLog):
        tail_proc = None
        
        try:
            log_filename = os.path.join(self.directories.command_dir, "log.txt")

            with open(log_filename, 'a') as build_log:
                tail_proc = SubprocessRunner.SubprocessRunner(["tail","-f",log_filename,"-n","+0"], log_function, log_function, enablePartialLineOutput=True)
                tail_proc.start()

                if dumpPreambleLog:
                    self.dumpPreambleLog(build_log, env, docker_image, command)
                else:
                    print >> build_log, "TestLooper Running command"
                    print >> build_log, command
                    print >> build_log, "********************************************"
                    print >> build_log
                    build_log.flush()

                    build_log.flush()

            logging.info("Running command: '%s'. Log: %s. Docker Image: %s", 
                command, 
                log_filename,
                docker_image.image if docker_image is not None else "<none>"
                )

            with open(os.path.join(self.directories.command_dir, "cmd.sh"), "w") as f:
                print >> f, command

            with open(os.path.join(self.directories.command_dir, "cmd_invoker.sh"), "w") as f:
                print >> f, "hostname testlooperworker"
                print >> f, "bash /test_looper/command/cmd.sh >> /test_looper/command/log.txt 2>&1"

            assert docker_image is not None

            with DockerWatcher.DockerWatcher(self.name_prefix) as watcher:
                container = watcher.run(
                    docker_image,
                    ["/bin/bash", "/test_looper/command/cmd_invoker.sh"],
                    volumes=self.volumesToExpose(),
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
                        ret_code = container.wait(timeout=HEARTBEAT_INTERVAL)
                    except requests.exceptions.ReadTimeout:
                        pass
                    except requests.exceptions.ConnectionError:
                        pass

                    log_function("")
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
            with open(os.path.join(targetDir, ".git_commit"), "w") as f:
                f.write(git_repo.standardCommitMessageFor(commitHash))
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

    def purge_build_cache(self, cacheSize=None):
        self.ensureDirectoryExists(self.directories.build_cache_dir)
        
        while self._is_build_cache_full(cacheSize if cacheSize is not None else self.max_build_cache_depth):
            self._remove_oldest_cached_build()

    def _is_build_cache_full(self, cacheSize):
        cache_count = len(os.listdir(self.directories.build_cache_dir))

        logging.info("Checking the build cache: there are %s items in it", cache_count)

        return cache_count > cacheSize

    def _remove_oldest_cached_build(self):
        def full_path(p):
            return os.path.join(self.directories.build_cache_dir, p)
        cached_builds = sorted([(os.path.getctime(full_path(p)), full_path(p))
                                for p in os.listdir(self.directories.build_cache_dir)])
        os.remove(cached_builds[0][1])

    @staticmethod
    def getDockerImageFromRepo(git_repo, commitHash, image):
        assert image.matches.Dockerfile

        pathToDockerfile = image.dockerfile

        source = git_repo.getFileContents(commitHash, pathToDockerfile)

        if source is None:
            raise Exception("No file found at %s in commit %s" % (pathToDockerfile, commitHash))

        return Docker.DockerImage.from_dockerfile_as_string(None, source, create_missing=True)

    def resolveEnvironment(self, environment):
        return self.resolver.resolveEnvironment(environment)

    def getDockerImage(self, testEnvironment, log_function):
        assert testEnvironment.matches.Environment
        assert testEnvironment.platform.matches.linux
        assert testEnvironment.image.matches.Dockerfile or testEnvironment.image.matches.DockerfileInline

        try:
            if testEnvironment.image.matches.Dockerfile:
                repoName = testEnvironment.image.repo
                commitHash = testEnvironment.image.commitHash

                git_repo = self.getRepoCacheByName(repoName)

                return self.getDockerImageFromRepo(git_repo, commitHash, testEnvironment.image)
            else:
                return Docker.DockerImage.from_dockerfile_as_string(None, testEnvironment.image.dockerfile_contents, create_missing=True)
        except Exception as e:
            log_function(time.asctime() + " TestLooper> Failed to build docker image:\n" + str(e))

        return None

    def testAndEnvironmentDefinitionFor(self, repoName, commitHash):
        return self.resolver.testAndEnvironmentDefinitionFor(repoName, commitHash)

    def repoDefinitionsFor(self, repoName, commitHash):
        return self.testAndEnvironmentDefinitionFor(repoName, commitHash)[2]

    def testDefinitionFor(self, repoName, commitHash, testName):
        return self.testAndEnvironmentDefinitionFor(repoName, commitHash)[0].get(testName)

    def runTest(self, testId, repoName, commitHash, testName, workerCallback, isDeploy):
        """Run a test (given by name) on a given commit and return a TestResultOnMachine"""
        self.cleanup()

        t0 = time.time()

        log_messages = []
        def log_function(msg=""):
            if isDeploy:
                if msg is not None:
                    workerCallback.terminalOutput(msg.replace("\n", "\r\n"))
            else:
                workerCallback.heartbeat(msg)

            if msg is not None:
                log_messages.append(msg)

        with self.callHeartbeatInBackground(log_function, "Resetting the repo to %s/%s" % (repoName, commitHash)):
            if not self.resetToCommit(repoName, commitHash):
                workerCallback.heartbeat("Failed to checkout code!\n")

        def executeTest():
            try:
                with self.callHeartbeatInBackground(log_function, "Extracting test definitions."):
                    testDefinition = self.testDefinitionFor(repoName, commitHash, testName)

                if not testDefinition:
                    log_function("No test named %s\n" % testName)
                    return False, {}

                if not isDeploy and testDefinition.matches.Build and self.artifactStorage.build_exists(repoName, commitHash, self.artifactKeyForBuild(testName)):
                    log_function("Build already exists\n")
                    return True, {}
                
                return self._run_task(testId, repoName, commitHash, testDefinition, log_function, workerCallback, isDeploy)
            except KeyboardInterrupt:
                log_function("\nInterrupted by Ctrl-C\n")
                return False, {}
            except:
                print "*******************"
                print traceback.format_exc()
                print "*******************"
                error_message = "Test failed because of exception: %s" % traceback.format_exc()
                logging.error(error_message)
                log_function(error_message)
                return False, {}


        success, individualTestSuccesses = executeTest()

        if isDeploy:
            return False, {}

        try:
            log_function(time.asctime() + " TestLooper> Uploading logfile.\n")

            path = os.path.join(self.directories.scratch_dir, "test_result.json")
            with open(path, "w") as f:
                f.write(
                    json.dumps(
                        {"success": success,
                         "individualTests": individualTestSuccesses,
                         "start_timestamp": t0,
                         "end_timestamp": time.time()
                        })
                    )
                    
            self.artifactStorage.uploadSingleTestArtifact(repoName, commitHash, testId, "test_result.json", path)

            path = os.path.join(self.directories.scratch_dir, "test_looper_log.txt")
            with open(path, "w") as f:
                f.write("".join(log_messages))

            self.artifactStorage.uploadSingleTestArtifact(repoName, commitHash, testId, "test_looper_log.txt", path)

        except:
            log_function("ERROR: Failed to upload the testlooper logfile to artifactStorage:\n\n%s" % traceback.format_exc())

        return success, individualTestSuccesses



    def extract_package(self, package_file, target_dir):
        with tarfile.open(package_file) as tar:
            root = tar.next()
            if root is None:
                raise Exception("Package %s is empty" % package_file)
            logging.info("Extracting package %s to %s", package_file, target_dir)
            tar.extractall(target_dir)

    def grabDependency(self, log_function, expose_as, dep, repoName, commitHash):
        target_dir = os.path.join(self.directories.test_inputs_dir, expose_as)

        if dep.matches.InternalBuild or dep.matches.ExternalBuild:
            if dep.matches.ExternalBuild:
                repoName, commitHash = dep.repo, dep.commitHash

            if not self.artifactStorage.build_exists(repoName, commitHash, self.artifactKeyForBuild(dep.name)):
                return "can't run tests because dependent external build %s doesn't exist" % (repoName + "/" + commitHash + "/" + dep.name)

            path = self._download_build(repoName, commitHash, dep.name, log_function)
            
            self.ensureDirectoryExists(target_dir)
            self.extract_package(path, target_dir)
            return None

        if dep.matches.Source:
            sourceArtifactName = self.artifactKeyForBuild("source")

            tarball_name = self._buildCachePathFor(dep.repo, dep.commitHash, "source")

            if not self.artifactStorage.build_exists(dep.repo, dep.commitHash, sourceArtifactName):
                log_function(time.asctime() + " TestLooper> Building source cache for %s/%s.\n" % (dep.repo, dep.commitHash))

                self.resetToCommitInDir(dep.repo, dep.commitHash, target_dir)

                with tarfile.open(tarball_name, "w:gz", compresslevel=1) as tf:
                    with DirectoryScope.DirectoryScope(target_dir):
                        tf.add(".")

                log_function(time.asctime() + " TestLooper> Resulting tarball at %s is %.2f MB.\n" %(tarball_name, os.stat(tarball_name).st_size / 1024.0**2))

                try:
                    log_function(
                        time.asctime() + " TestLooper> Uploading %s to %s/%s/%s\n" % 
                            (tarball_name, dep.repo, dep.commitHash, sourceArtifactName)
                        )
                    self.artifactStorage.upload_build(dep.repo, dep.commitHash, sourceArtifactName, tarball_name)
                except:
                    logging.error("Failed to upload package '%s':\n%s",
                          tarball_name,
                          traceback.format_exc()
                          )
            else:
                if not os.path.exists(tarball_name):
                    log_function(time.asctime() + " TestLooper> Downloading source cache for %s/%s.\n" % (dep.repo, dep.commitHash))
                
                    self.artifactStorage.download_build(dep.repo, dep.commitHash, sourceArtifactName, tarball_name)

                log_function(time.asctime() + " TestLooper> Extracting source cache for %s/%s.\n" % (dep.repo, dep.commitHash))

                self.extract_package(tarball_name, target_dir)

            return None

        return "Unknown dependency type: %s" % dep

    def getEnvironmentAndDependencies(self, testId, repoName, commitHash, test_definition, log_function):
        with self.callHeartbeatInBackground(log_function):
            environment = self.resolveEnvironment(test_definition.environment)
            if environment.matches.Import:
                raise Exception("Environment didn't resolve to a real environment: inheritance is %s", environment.inheritance)
            environment = TestDefinition.apply_environment_substitutions(environment)

        env_overrides = self.environment_variables(testId, repoName, commitHash, environment, test_definition)

        #update the test definition to resolve dependencies
        test_definition = TestDefinition.apply_test_substitutions(test_definition, environment, env_overrides)

        all_dependencies = {}
        all_dependencies.update(environment.dependencies)
        all_dependencies.update(test_definition.dependencies)

        if self.hardwareConfig.cores > 2:
            lock = threading.Lock()

            def heartbeatWithLock(msg=None):
                with lock:
                    log_function(msg)

            with self.callHeartbeatInBackground(
                    heartbeatWithLock, 
                    "Pulling dependencies:\n%s" % "\n".join(["\t" + str(x) for x in all_dependencies.values()])
                    ):

                results = {}

                def callFun(expose_as, dep):
                    for tries in xrange(3):
                        try:
                            results[expose_as] = self.grabDependency(heartbeatWithLock, expose_as, dep, repoName, commitHash)
                            heartbeatWithLock(time.asctime() + " TestLooper> Done pulling %s.\n" % dep)
                            return
                        except Exception as e:
                            if tries < 2:
                                heartbeatWithLock(time.asctime() + " TestLooper> Failed to pull %s because %s, but retrying.\n" % (dep, str(e)))

                            results[expose_as] = traceback.format_exc()

                waiting_threads = [threading.Thread(target=callFun, args=(expose_as,dep))
                                for (expose_as, dep) in all_dependencies.iteritems()]

                running_threads = []

                simultaneous = self.hardwareConfig.cores

                while running_threads + waiting_threads:
                    running_threads = [x for x in running_threads if x.isAlive()]
                    while len(running_threads) < simultaneous and waiting_threads:
                        t = waiting_threads.pop(0)
                        t.start()
                        running_threads.append(t)
                    time.sleep(1.0)

                for e in all_dependencies:
                    if results[e] is not None:
                        raise Exception("Failed to download dependency %s: %s" % (all_dependencies[e], results[e]))
        else:
            for expose_as, dep in all_dependencies.iteritems():
                with self.callHeartbeatInBackground(log_function, "Pulling dependency %s" % dep):
                    errStringOrNone = self.grabDependency(log_function, expose_as, dep, repoName, commitHash)

                if errStringOrNone is not None:
                    raise Exception(errStringOrNone)

        return environment, all_dependencies, test_definition

    def _run_task(self, testId, repoName, commitHash, test_definition, log_function, workerCallback, isDeploy):
        try:
            environment, all_dependencies, test_definition = \
                self.getEnvironmentAndDependencies(testId, repoName, commitHash, test_definition, log_function)
        except Exception as e:
            logging.error(traceback.format_exc())
            log_function("\n\nTest failed because of exception:\n" + traceback.format_exc() + "\n")
            return False, {}

        if test_definition.matches.Build:
            command = test_definition.buildCommand
            cleanup_command = test_definition.cleanupCommand
        elif test_definition.matches.Test:
            command = test_definition.testCommand
            cleanup_command = test_definition.cleanupCommand
        elif test_definition.matches.Deployment:
            command = test_definition.deployCommand
            cleanup_command = ""
        else:
            assert False, test_definition

        logging.info("Environment is: %s", environment)

        if environment.image.matches.AMI:
            image = NAKED_MACHINE

            command = environment.image.setup_script_contents + "\n\n" + command
        else:
            with self.callHeartbeatInBackground(log_function, "Extracting docker image for environment %s" % environment):
                image = self.getDockerImage(environment, log_function)

        if image is None:
            is_success = False
            if isDeploy:
                log_function("Couldn't find docker image...")
                return False, {}
        else:
            logging.info("Machine %s is starting run for %s %s. Command: %s",
                         self.machineId,
                         repoName, 
                         commitHash,
                         command)

            if isDeploy:
                self._run_deployment(command, test_definition.variables, workerCallback, image)
                return False, {}
            else:
                log_function(time.asctime() + " TestLooper> Starting Test Run\n")

                is_success = self._run_test_command(
                    command,
                    test_definition.timeout or 60 * 60, #1 hour if unspecified
                    test_definition.variables,
                    log_function,
                    image,
                    dumpPreambleLog=True
                    )

                #run the cleanup_command if necessary
                if self.wants_to_run_cleanup() and cleanup_command.strip() and not self._run_test_command(
                        cleanup_command,
                        test_definition.timeout or 60 * 60, #1 hour if unspecified
                        test_definition.variables,
                        log_function,
                        image,
                        dumpPreambleLog=False
                        ):
                    is_success = False

        if is_success and test_definition.matches.Build:
            with self.callHeartbeatInBackground(log_function, "Uploading build artifacts."):
                if not self._upload_build(repoName, commitHash, test_definition.name):
                    logging.error('Failed to upload build for %s/%s/%s', repoName, commitHash, test_definition.name)
                    is_success = False

        log_function("")
        
        logging.info("machine %s uploading artifacts for test %s", self.machineId, testId)

        individualTestSuccesses = {}

        with self.callHeartbeatInBackground(log_function, "Uploading test artifacts."):
            self.artifactStorage.uploadTestArtifacts(
                repoName,
                commitHash,
                testId,
                self.directories.test_output_dir,
                set(["test_looper_log.txt", "test_result.json"])
                )

            testSummaryJsonPath = os.path.join(self.directories.test_output_dir, "testSummary.json")

            if os.path.exists(testSummaryJsonPath):
                try:
                    individualTestSuccesses = json.loads(open(testSummaryJsonPath,"r").read())
                    if not isinstance(individualTestSuccesses, dict):
                        raise Exception("testSummary.json should be a dict from str to bool")
                    individualTestSuccesses = {str(k): bool(v) for k,v in individualTestSuccesses.iteritems()}
                except Exception as e:
                    individualTestSuccesses = {}
                    log_function("Failed to pull in testSummary.json: " + str(e))
                    logging.error("Error processing testSummary.json:\n%s", traceback.format_exc())

        return is_success, individualTestSuccesses

    def artifactKeyForBuild(self, testName):
        return testName.replace("/", "_") + ".tar.gz"

    def _upload_build(self, repoName, commitHash, testName):
        #upload all the data in our directory
        tarball_name = os.path.join(
            self.directories.build_cache_dir, 
            self.artifactKeyForBuild(testName)
            )

        if not os.path.exists(tarball_name):
            logging.info("Tarballing %s into %s", self.directories.build_output_dir, tarball_name)

            with tarfile.open(tarball_name, "w:gz", compresslevel=1) as tf:
                with DirectoryScope.DirectoryScope(self.directories.build_output_dir):
                    tf.add(".")

            logging.info("Resulting tarball at %s is %.2f MB", tarball_name, os.stat(tarball_name).st_size / 1024.0**2)
        else:
            logging.warn("A build for %s/%s/%s already exists at %s", repoName, commitHash, testName, tarball_name)

        try:
            logging.info("Uploading %s to %s", tarball_name, self.artifactKeyForBuild(testName))

            self.artifactStorage.upload_build(repoName, commitHash, self.artifactKeyForBuild(testName), tarball_name)
            return True
        except:
            logging.error("Failed to upload package '%s' to %s/%s\n%s",
                          tarball_name,
                          repoName, 
                          commitHash,
                          traceback.format_exc()
                          )
            return False

    def _buildCachePathFor(self, repoName, commitHash, testName):
        return os.path.join(
            self.directories.build_cache_dir,
            (repoName + "/" + commitHash + "." + self.artifactKeyForBuild(testName)).replace("/", "_")
            )

    def _download_build(self, repoName, commitHash, testName, log_function):
        path = self._buildCachePathFor(repoName, commitHash, testName)
        
        if not os.path.exists(path):
            log_function("Downloading build for %s/%s test %s to %s.\n" % (repoName, commitHash, testName, path))
            self.artifactStorage.download_build(repoName, commitHash, self.artifactKeyForBuild(testName), path)

        return path

    def environment_variables(self, testId, repoName, commitHash, environment, test_definition):
        res = {}
        res.update({
            'TEST_REPO': repoName,
            'REVISION': commitHash,
            'TEST_CORES_AVAILABLE': str(self.hardwareConfig.cores),
            'TEST_RAM_GB_AVAILABLE': str(self.hardwareConfig.ram_gb),
            'PYTHONUNBUFFERED': "TRUE",
            'HOSTNAME': "testlooperworker"
            })

        if environment.image.matches.AMI:
            res.update({
                'TEST_SRC_DIR': self.directories.repo_copy_dir,
                'TEST_INPUTS': self.directories.test_inputs_dir,
                'TEST_SCRATCH_DIR': self.directories.scratch_dir,
                'TEST_OUTPUT_DIR': self.directories.test_output_dir,
                'TEST_BUILD_OUTPUT_DIR': self.directories.build_output_dir,
                'TEST_CCACHE_DIR': self.directories.ccache_dir
                })
        else:
            res.update({
                'TEST_SRC_DIR': "/test_looper/src",
                'TEST_INPUTS': "/test_looper/test_inputs",
                'TEST_SCRATCH_DIR': "/test_looper/scratch",
                'TEST_OUTPUT_DIR': "/test_looper/output",
                'TEST_BUILD_OUTPUT_DIR': "/test_looper/build_output",
                'TEST_CCACHE_DIR': "/test_looper/ccache"
                })

        if testId is not None:
            res['TEST_LOOPER_TEST_ID'] = testId

        return res
