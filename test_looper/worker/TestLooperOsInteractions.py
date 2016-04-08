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

import test_looper.client.PerformanceTestReporter as PerformanceTestReporter
import test_looper.core.DirectoryScope as DirectoryScope
import test_looper.core.TimerQueue as TimerQueue
import test_looper.worker.TestLooperClient as TestLooperClient

timerQueue = TimerQueue.TimerQueue(16)

TestLooperDirectories = collections.namedtuple(
    'TestLooperDirectories',
    ['repo_dir', 'test_data_dir', 'build_cache_dir', 'ccache_dir']
    )

class TestLooperOsInteractions(object):
    def __init__(self, test_looper_directories):
        self.directories = test_looper_directories
        self.max_build_cache_depth = 10
        self.heartbeatInterval = TestLooperClient.TestLooperClient.HEARTBEAT_INTERVAL
        self.ownSessionId = os.getsid(0)
        self.ownProcGroupId = os.getpgrp()


    @staticmethod
    def directoryScope(directoryScope):
        return DirectoryScope.DirectoryScope(directoryScope)


    def initializeTestLooperEnvironment(self):
        self.clearOldTestResults()


    def cleanup(self):
        self.killLeftoverProcesses()
        self.removeDanglingDockerImages()
        logging.info("Clearing data directory: %s", self.directories.test_data_dir)
        assert self.directories.test_data_dir is not None
        assert self.directories.test_data_dir != ''
        cmd = 'docker run --rm -v %s:/volume ubuntu:14.04 bash -c "rm -rf /volume/*"'
        output = subprocess.check_output(cmd % self.directories.test_data_dir,
                                         shell=True)
        logging.info("Cleared data directory: %s", output)
        self.clearOldTestResults()


    @staticmethod
    def extract_package(package_file, target_dir):
        with tarfile.open(package_file) as tar:
            root = tar.next()
            if root is None:
                raise Exception("Package %s is empty" % package_file)
            package_dir = os.path.join(target_dir, root.name)
            logging.info("Extracting package to %s", package_dir)
            tar.extractall(target_dir)
            return package_dir


    def killLeftoverProcesses(self):
        """Kill all processes in our session that are in a different process group.
        TestLooperOsInteractions starts child processes in new process groups.
        """
        allProcIds = [int(pid) for pid in os.listdir('/proc') if pid.isdigit()]
        pidsToKill = [
            pid for pid in allProcIds
            if os.getsid(pid) == self.ownSessionId and os.getpgid(pid) != self.ownProcGroupId
            ]

        logging.info("Killing running processes: %s", pidsToKill)
        for procGroup in set([os.getpgid(pid) for pid in pidsToKill]):
            os.killpg(procGroup, signal.SIGKILL)


    @staticmethod
    def removeDanglingDockerImages():
        cmd = 'docker images -qf dangling=true | xargs --no-run-if-empty docker rmi'
        output = subprocess.check_output(cmd, shell=True)
        logging.info("Deleted dangling docker images: %s", output)


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


    def run_command(self, command, log_filename, build_env, timeout, heartbeat):
        with open(log_filename, 'a') as build_log:
            env = dict(os.environ)
            env.update(build_env)
            logging.info("Running command: '%s'. Log: %s", command, log_filename)
            result = self.runSubprocess(timeout,
                                        heartbeat,
                                        command,
                                        stdout=build_log,
                                        stderr=build_log,
                                        shell=True,
                                        env=env)
            if not result:
                logging.error("Command failed.")
            return result


    def runSubprocess(self, timeout, heartbeatFunction, *args, **kwargs):
        proc = subprocess.Popen(*args, **kwargs)

        # subprocess doesn't have time wait...
        def waiter():
            proc.wait()

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
        return not is_timeout and proc.returncode == 0


    @staticmethod
    def pullLatest():
        logging.info("Fetching from origin")
        subprocess.check_call(['git fetch origin'], shell=True)

    def resetToCommit(self, revision):
        logging.info("Resetting to revision %s", revision)
        toCall = 'git reset --hard ' + revision
        attempts = 0
        while attempts < 2:
            try:
                attempts += 1
                subprocess.check_call([toCall], shell=True)
                return True
            except subprocess.CalledProcessError:
                logging.info("Failed to reset the repo to %s. Fetching and trying again.", revision)
                self.pullLatest()
        return False


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


    def resetDepot(self, revision, artifacts):
        self.resetToCommit(revision)
        self.cleanup()

        if artifacts is not None:
            subprocess.call(['rm -rf ' + artifacts], shell=True)


    @staticmethod
    def extractPerformanceTests(outPerformanceTestsFile, testName):
        if os.path.exists(outPerformanceTestsFile):
            performanceTestsList = \
                PerformanceTestReporter.loadTestsFromFile(outPerformanceTestsFile)

            #verify that we can dump this as json. If we fail, we'll still be able to understand
            #what happened
            simplejson.dumps(performanceTestsList)

            return performanceTestsList
        else:
            return []


    @staticmethod
    def deleteFileIfItExists(filename):
        subprocess.call(['rm -rf %s' % filename], shell=True)


    @staticmethod
    def writeTextToFile(filename, text):
        subprocess.call(['''sh -c "echo '%s' > %s"''' % (text, filename)], shell=True)


    @staticmethod
    def pickPerformanceTestFileLocation(testOutputDir):
        return os.path.join(testOutputDir, 'performanceMeasurements.json')


    def executeScript(self,
                      timeoutForProc,
                      heartbeatFunction,
                      scriptToRun,
                      environmentOverrides,
                      testOutputDir,
                      outlogfilePath):
        env = dict(os.environ)

        env['LOOPER_DATA_DIR'] = testOutputDir
        env['PYTHONPATH'] = os.getcwd()
        env['WORKSPACE'] = os.getcwd()
        env['CUMULUS_DATA_DIR'] = self.directories.test_data_dir

        self.writeTextToFile(
            os.path.join(os.getcwd(), "ufora", "config", "config.cfg"),
            "ROOT_DATA_DIR=" + self.directories.test_data_dir
            )

        env.update(environmentOverrides)

        with open(outlogfilePath, 'w', 1) as outlogfile:
            return self.runSubprocess(timeoutForProc,
                                      heartbeatFunction,
                                      ['ulimit -c unlimited; ' + scriptToRun],
                                      shell=True,
                                      stdout=outlogfile,
                                      stderr=outlogfile,
                                      env=env,
                                      preexec_fn=os.setsid)


    def build(self, commit_id, build_command, env, output_dir, timeout, heartbeat):
        build_log = os.path.join(output_dir, 'build.log')
        build_env = {
            'BUILD_COMMIT': commit_id,
            'OUTPUT_DIR': output_dir,
            'CCACHE_DIR': self.directories.ccache_dir
            }
        build_env.update(env)

        with self.directoryScope(self.directories.repo_dir):
            return self.resetToCommit(commit_id) and \
                   self.run_command(build_command, build_log, build_env, timeout, heartbeat)


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


    @staticmethod
    def create_test_virtualenv(test_dir, client_version):
        venv_dir = os.path.join(test_dir, 'venv')
        virtualenv.create_environment(venv_dir, site_packages=True)
        pip = os.path.join(venv_dir, 'bin', 'pip')
        subprocess.check_call([pip, 'install', 'test-looper==%s' % client_version],
                              stdout=sys.stdout,
                              stderr=sys.stderr)
        return os.path.join(venv_dir, 'bin', 'activate')


    @staticmethod
    def uploadTestArtifacts(bucket, keyPrefix, testOutputDir):
        def uploadFile(path, semaphore):
            try:
                logging.info("Uploading %s", path)
                headers = {}
                if '.log' in path:
                    headers['Content-Type'] = 'text/plain'
                if path.endswith('.gz'):
                    headers['Content-Encoding'] = 'gzip'
                key = bucket.new_key(keyPrefix + '/' + os.path.split(path)[-1])
                key.set_contents_from_filename(path, headers=headers)
            except:
                logging.error("Failed to upload %s:\n%s", path, traceback.format_exc())
            finally:
                semaphore.release()

        for logFile in os.listdir(testOutputDir):
            if logFile.endswith(('.log', '.out')):
                logFile = os.path.join(testOutputDir, logFile)
                subprocess.call(['gzip %s' % logFile], shell=True)


        sem = threading.Semaphore(0)
        for logFile in os.listdir(testOutputDir):
            logFile = os.path.join(testOutputDir, logFile)
            timerQueue.enqueueWorkItem(uploadFile, (logFile, sem))

        for logFile in os.listdir(testOutputDir):
            sem.acquire()


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
