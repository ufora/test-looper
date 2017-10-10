import collections
import errno
import logging
import os
import shutil
import signal
import simplejson
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import virtualenv
import psutil

import test_looper.core.SubprocessRunner as SubprocessRunner
import test_looper.core.tools.Git as Git
import test_looper.core.DirectoryScope as DirectoryScope
import test_looper.worker.TestLooperClient as TestLooperClient
import test_looper.core.tools.Docker as Docker


class TestLooperDirectories:
    def __init__(self, repo_dir, test_data_dir, build_cache_dir, ccache_dir):
        self.repo_dir = repo_dir
        self.test_data_dir = test_data_dir
        self.build_cache_dir = build_cache_dir
        self.ccache_dir = ccache_dir

    def to_expose(self):
        return [self.repo_dir, self.test_data_dir, self.build_cache_dir, self.ccache_dir]

class TestLooperOsInteractions(object):
    def __init__(self, test_looper_directories, source_control, docker_repo):
        self.directories = test_looper_directories

        logging.info("Ensuring existence of %s", self.directories.repo_dir)

        self.ensureDirectoryExists(self.directories.repo_dir)
        self.ensureDirectoryExists(self.directories.test_data_dir)
        self.ensureDirectoryExists(self.directories.build_cache_dir)
        self.ensureDirectoryExists(self.directories.ccache_dir)

        self.max_build_cache_depth = 10
        self.heartbeatInterval = TestLooperClient.TestLooperClient.HEARTBEAT_INTERVAL
        self.ownSessionId = os.getsid(0)
        self.ownProcGroupId = os.getpgrp()
        self.source_control = source_control
        self.git_repo = Git.Git(self.directories.repo_dir)
        self.docker_repo = docker_repo

    @staticmethod
    def directoryScope(directoryScope):
        return DirectoryScope.DirectoryScope(directoryScope)

    def initializeTestLooperEnvironment(self):
        self.initializeGitRepo()
        self.clearOldTestResults()

    def initializeGitRepo(self):
        if not self.git_repo.isInitialized():
            self.git_repo.cloneFrom(self.source_control.cloneUrl())

    def cleanup(self):
        self.killLeftoverProcesses()
        Docker.DockerImage.removeRunningDockerContainers()
        Docker.DockerImage.removeDanglingDockerImages()
        logging.info("Clearing data directory: %s", self.directories.test_data_dir)
        assert self.directories.test_data_dir is not None
        assert self.directories.test_data_dir != ''

        self.clearDirectoryAsDocker(self.directories.test_data_dir)
        self.clearOldTestResults()
        
    def clearDirectoryAsDocker(self, path):
        image = Docker.DockerImage("ubuntu:16.04")
        image.run("rm -rf %s/*" % path, volumes={path:path})


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

    def killLeftoverProcesses(self):
        """Kill all processes in our session that are in a different process group.
        TestLooperOsInteractions starts child processes in new process groups.
        """

        self_and_parents = []
        p = psutil.Process()
        
        while p is not None:
            self_and_parents.append(os.getpgid(p.pid))
            p = p.parent()


        allProcIds = [int(pid) for pid in os.listdir('/proc') if pid.isdigit()]
        pidsToKill = [
            pid for pid in allProcIds
            if os.getsid(pid) == self.ownSessionId and os.getpgid(pid) != self.ownProcGroupId
                and pid not in self_and_parents
            ]

        logging.info("Killing running processes: %s", pidsToKill)
        for procGroup in set([os.getpgid(pid) for pid in pidsToKill]):
            if procGroup not in self_and_parents:
                os.killpg(procGroup, signal.SIGKILL)


    def clearOldTestResults(self):
        maxSize = 15*1024*1024 #15GB
        daysToKeep = 5
        while self.directorySize(self.baseDataDir()) > maxSize and daysToKeep > 0:
            os.system("find %s -maxdepth 1 -mtime +%d | xargs rm -rf" % \
                      (self.baseDataDir(), daysToKeep))
            daysToKeep -= 1

        if self.directorySize(self.baseDataDir()) > maxSize:
            logging.warn("Too much test data in one day. Deleting all test data files.")
            os.system("rm -rf %s/*" % self.baseDataDir())


    @staticmethod
    def directorySize(path):
        if not os.path.exists(path):
            return 0

        return int(subprocess.check_output('du -s "%s"' % path, shell=True).split()[0])

    def baseDataDir(self):
        return self.directories.test_data_dir

    def run_command(self, command, log_filename, build_env, timeout, heartbeat, docker_image):
        logging.info("build_env: %s", build_env)

        with open(log_filename, 'a') as build_log:
            env = dict(os.environ)
            env.update(build_env)

            print >> build_log, "********************************************"

            print >> build_log, "TestLooper Environment Variables:"
            for e in sorted(build_env):
                print >> build_log, "\t%s=%s" % (e, env[e])
            print >> build_log


            if docker_image is not None:
                print >> build_log, "DockerImage is ", docker_image.image
            build_log.flush()

            print >> build_log, "Working Directory: ", os.getcwd()
            build_log.flush()

            print >> build_log, "TestLooper Running command ", command
            build_log.flush()

            print >> build_log, "********************************************"
            print >> build_log

            logging.info("Running command: '%s'. Log: %s", command, log_filename)

            def onOut(msg):
                print >> build_log, msg
            def onErr(msg):
                print >> build_log, msg
            
            if docker_image is None:
                subprocess = SubprocessRunner.SubprocessRunner(command, onOut, onErr, shell=True)
            else:
                cmds = docker_image.subprocessCommandsToRun(
                    command, 
                    self.directories.to_expose(),
                    build_env
                    )
                
                try:
                    subprocess = SubprocessRunner.SubprocessRunner(cmds, onOut, onErr, shell=False)

                    result = self.runSubprocess(subprocess,
                                                timeout,
                                                heartbeat)
                    if not result:
                        logging.error("Command failed.")
                    return result
                except:
                    logging.error("Failed running %s", " ".join(cmds))
                    raise


    def runSubprocess(self, proc, timeout, heartbeatFunction):
        # subprocess doesn't have time wait...
        def waiter():
            proc.wait()
        
        proc.start()

        t = threading.Thread(target=waiter)
        t.start()

        t0 = time.time()
        interrupted = False
        is_timeout = False
        try:
            while t.isAlive() and time.time() - t0 < timeout:
                heartbeatFunction()
                t.join(self.heartbeatInterval)
        except:
            interrupted = True

        if not interrupted:
            # don't call heartbeatFunction if it already raised an
            # exception.
            heartbeatFunction()

        if t.isAlive():
            is_timeout = True
            logging.warn("Process still running after %s seconds. Terminating...",
                         time.time() - t0)
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait()
        t.join()
        return not is_timeout and proc.wait() == 0


    @staticmethod
    def pullLatest():
        logging.info("Fetching from origin")
        subprocess.check_call(['git fetch origin'], shell=True)

    def resetToCommit(self, revision, log_filename):
        logging.info("Resetting to revision %s", revision)
        toCall = 'git reset --hard ' + revision
        attempts = 0
        while attempts < 2:
            try:
                attempts += 1
                subprocess.check_call([toCall], shell=True)
                return True
            except subprocess.CalledProcessError as e:
                logging.info("Failed to reset the repo to %s. Fetching and trying again. %s", revision, traceback.format_exc())
                self.pullLatest()

        logging.error("Failed to reset repo after %d attempts", attempts)
        return False


    @staticmethod
    def updateSubmodules(log_filename):
        with open(log_filename, 'a') as f:
            command = 'git submodule deinit -f . && git submodule init && git submodule update'
            f.write(command + '\n')
            subprocess.check_call(
                command,
                stdout=f,
                stderr=f,
                shell=True)

    @staticmethod
    def ensureDirectoryExists(path):
        try:
            os.makedirs(path)
        except os.error as e:
            if e.errno != errno.EEXIST:
                raise

    def createNextTestDirForCommit(self, commitId):
        revisionDir = os.path.join(self.baseDataDir(), commitId)

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


    @staticmethod
    def pickPerformanceTestFileLocation(testOutputDir):
        return os.path.join(testOutputDir, 'performanceMeasurements.json')

    def build(self, commit_id, build_command, env, output_dir, timeout, heartbeat, docker_image):
        build_log = os.path.join(output_dir, 'build.log')
        build_env = {
            'BUILD_COMMIT': commit_id,
            'OUTPUT_DIR': output_dir,
            'CCACHE_DIR': self.directories.ccache_dir
            }
        build_env.update(env)

        with self.directoryScope(self.directories.repo_dir):
            return self.resetToCommit(commit_id, build_log) and \
                   self.run_command(build_command, build_log, build_env, timeout, heartbeat, docker_image)


    def cache_build(self, commit_id, build_package):
        while self.is_build_cache_full():
            self.remove_oldest_cached_build()
        cache_dir = os.path.join(self.directories.build_cache_dir, commit_id)
        if not os.path.exists(cache_dir):
            os.mkdir(cache_dir)
        shutil.copy(build_package, cache_dir)

    def is_build_cache_full(self):
        return len(os.listdir(self.directories.build_cache_dir)) >= self.max_build_cache_depth


    def remove_oldest_cached_build(self):
        def full_path(p):
            return os.path.join(self.directories.build_cache_dir, p)
        cached_builds = sorted([(os.path.getctime(full_path(p)), full_path(p))
                                for p in os.listdir(self.directories.build_cache_dir)])
        shutil.rmtree(cached_builds[0][1])


    def find_cached_build(self, commit_id):
        build_path = os.path.join(self.directories.build_cache_dir, commit_id)
        if os.path.exists(build_path):
            return os.path.join(build_path, os.listdir(build_path)[0])

    def getDockerImage(self, commit_id, dockerConf, output_dir):
        try:
            if 'native' in dockerConf:
                return None

            if 'tag' in dockerConf:
                tagname = dockerConf['tag']

                for char in tagname:
                    if not (char.isalnum() or char in ".-_:"):
                        raise Exception("Invalid tag name: " + tagname)

                if self.docker_repo is None:
                    image_name = dockerConf["tag"]
                else:
                    image_name = self.docker_repo + "/" + dockerConf["tag"]

                d = Docker.DockerImage(image_name)

                if not d.pull():
                    raise Exception("Couldn't find docker explicitly named image %s" % d.image)

                return d

            if 'dockerfile' in dockerConf:
                source = self.source_control.source_repo.getFileContents(commit_id, dockerConf["dockerfile"])
                if source is None:
                    raise Exception("No file found at %s in commit %s" % (dockerConf["dockerfile"], commit_id))

                return Docker.DockerImage.from_dockerfile_as_string(self.docker_repo, source, create_missing=True)

            raise Exception("No docker configuration was provided. Test should define one of " + 
                    "native, tag, or dockerfile"
                    )
        except Exception as e:
            self.ensureDirectoryExists(output_dir)
            with open(os.path.join(output_dir,"docker_configuration_error.log"),"w") as f:
                print >> f, "Failed to get a docker image configured by %s:\n\n%s" % (
                    dockerConf,
                    traceback.format_exc()
                    )

        return None

    def protocolMismatchObserved(self):
        self.abortTestLooper("test-looper server is on a different protocol version than we are.")

    @staticmethod
    def abortTestLooper(reason):
        logging.info(reason)
        logging.info(
            "Restarting. We expect 'upstart' to reboot us with an up-to-date copy of the code"
            )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
