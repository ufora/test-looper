import collections
import logging
import os
import socket
import time
import traceback
import threading

import test_looper.core.TestResult as TestResult
import test_looper.worker.TestLooperClient as TestLooperClient
import test_looper.core.TestScriptDefinition as TestScriptDefinition

HEARTBEAT_INTERVAL = TestLooperClient.TestLooperClient.HEARTBEAT_INTERVAL


class TestInterruptException(Exception):
    pass

TestLooperMachineInfo = collections.namedtuple(
    'TestLooperMachineInfo',
    [
        'machineName',
        'internalIpAddress',
        'coreCount',
        'availabilityZone',
        'instanceType'
    ]
    )

TestLooperSettings = collections.namedtuple(
    'TestLooperSettings',
    [
        'osInteractions',
        'testLooperClientFactory',
        'artifactsFileName',
        'timeout',
        'awsConnector',
        'coreDumpsDir'
    ])

class TestLooperWorker(object):
    perf_test_output_file = 'performanceMeasurements.json'

    def __init__(self,
                 testLooperSettings,
                 testLooperMachineInfo,
                 timeToSleepWhenThereIsNoWork=2.0
                ):
        self.settings = testLooperSettings
        self.ownMachineInfo = testLooperMachineInfo
        self.timeToSleepWhenThereIsNoWork = timeToSleepWhenThereIsNoWork
        self.stopEvent = threading.Event()

        self.heartbeatResponse = None
        self.testLooperClient = None


    def stop(self):
        self.stopEvent.set()


    def startTestLoop(self):
        try:
            socketErrorCount = 0
            waitTime = 0
            while not self.stopEvent.is_set():
                try:
                    waitTime = self.mainTestingIteration()
                    socketErrorCount = 0
                except TestLooperClient.ProtocolMismatchException:
                    logging.info("protocol mismatch observed on %s: %s",
                                 self.ownMachineInfo.machineName,
                                 traceback.format_exc())
                    return self.settings.osInteractions.protocolMismatchObserved()
                except socket.error:
                    logging.info("Can't connect to server")
                    socketErrorCount += 1
                    if socketErrorCount > 24:
                        return self.settings.osInteractions.abortTestLooper(
                            "Unable to communicate with server."
                            )
                    waitTime = 5.0
                except Exception as e:
                    waitTime = 1.0
                    logging.error(
                        "Exception %s on %s: %s",
                        type(e),
                        self.ownMachineInfo.machineName,
                        traceback.format_exc()
                        )

                if waitTime > 0:
                    self.stopEvent.wait(waitTime)

        finally:
            logging.info("Machine %s is exiting main testing loop",
                         self.ownMachineInfo.machineName)


    def mainTestingIteration(self):
        logging.info("Machine %s is starting a new test loop iteration",
                     self.ownMachineInfo.machineName)
        self.heartbeatResponse = TestResult.TestResult.HEARTBEAT_RESPONSE_ACK
        self.testLooperClient = self.settings.testLooperClientFactory()

        task = self.testLooperClient.getTask(
            self.ownMachineInfo.machineName,
            self.ownMachineInfo.internalIpAddress,
            self.ownMachineInfo.coreCount,
            self.ownMachineInfo.instanceType
            )

        if task is None:
            logging.info("Machine %s has nothing to do. Waiting.",
                         self.ownMachineInfo.machineName)
            return self.timeToSleepWhenThereIsNoWork

        logging.info("Machine %s is starting task %s",
                     self.ownMachineInfo.machineName,
                     task)
        self.run_task(task)
        return 0


    def run_task(self, task):
        test = TestResult.TestResult.fromJson(task['test'])
        logging.info("Machine %s is working on testId %s, test %s, for commit %s",
                     self.ownMachineInfo.machineName,
                     test.testId,
                     test,
                     test.commitId)
        self.settings.osInteractions.cleanup()

        try:
            if test.testName == 'build':
                result = self.run_build_task(test, task['testScriptDefinition']['command'])
            else:
                assert self.ownMachineInfo.machineName in test.machineToInternalIpMap, \
                    (test.machine,
                     test.machineToInternalIpMap,
                     self.ownMachineInfo.machineName)

                testScriptDefinition = TestScriptDefinition.TestScriptDefinition.fromJson(
                    task['testScriptDefinition']
                    )
                assert testScriptDefinition.testName == test.testName

                result = self.run_test_task(test, testScriptDefinition)
        except TestLooperClient.ProtocolMismatchException:
            raise
        except:
            error_message = "Test failed because of exception: %s" % traceback.format_exc()
            logging.error(error_message)
            result = self.create_test_result(False, test, error_message)

        logging.info("Machine %s publishing test results: %s",
                     self.ownMachineInfo.machineName,
                     result)
        self.testLooperClient.publishTestResult(result)


    def run_build_task(self, test, build_command):
        task_id = test.testId
        commit_id = test.commitId

        if self.settings.awsConnector.build_exists(self.s3_key_name_for_commit(commit_id)):
            return self.create_test_result(True, test)

        def heartbeat():
            return self.sendHeartbeat(self.testLooperClient, task_id, commit_id)

        os_interactions = self.settings.osInteractions
        build_output_dir = os_interactions.createNextTestDirForCommit(commit_id)
        is_success = os_interactions.build(commit_id,
                                           build_command,
                                           build_output_dir,
                                           self.settings.timeout,
                                           heartbeat) and \
                     self.upload_build(commit_id, build_output_dir)
        if not is_success:
            logging.info("Failed to build commit: %s", commit_id)
            os_interactions.uploadTestArtifacts(
                self.settings.awsConnector.get_test_result_bucket(),
                '%s/%s' % (task_id, self.ownMachineInfo.machineName),
                build_output_dir
                )
        return self.create_test_result(is_success, test)


    def run_test_task(self, target_test, test_definition):
        commit_id = target_test.commitId
        test_dir = self.settings.osInteractions.createNextTestDirForCommit(commit_id)

        package_file = self.download_build(commit_id, test_dir)
        package_dir = self.settings.osInteractions.extract_package(package_file, test_dir)

        with self.settings.osInteractions.directoryScope(package_dir):
            test_output_dir = os.path.join(test_dir, 'output')
            self.settings.osInteractions.ensureDirectoryExists(test_output_dir)

            env_overrides = self.test_env_overrides(target_test, test_output_dir)

            def heartbeat():
                return self.sendHeartbeat(self.testLooperClient, target_test.testId, commit_id)

            logging.info("Machine %s is starting run for %s. Command: %s",
                         self.ownMachineInfo.machineName,
                         commit_id,
                         test_definition.testScriptPath)
            is_success = self.runTestUsingScript(test_definition.testScriptPath,
                                                 env_overrides,
                                                 heartbeat,
                                                 test_output_dir)
            test_result = self.create_test_result(is_success, target_test)

            self.capture_perf_results(target_test.testName,
                                      os.path.join(test_output_dir, self.perf_test_output_file),
                                      test_result)
            if not is_success:
                heartbeat()
                logging.info("machine %s uploading artifacts", self.ownMachineInfo.machineName)
                self.settings.osInteractions.uploadTestArtifacts(
                    self.settings.awsConnector.get_test_result_bucket(),
                    "%s/%s" % (target_test.testId, self.ownMachineInfo.machineName),
                    test_output_dir
                    )

            return test_result


    def create_test_result(self, is_success, test, message=None):
        logging.info("publishing result for test %s - success: %s", test, is_success)
        result = TestResult.TestResultOnMachine(is_success,
                                                test.testId,
                                                test.commitId,
                                                [], [],
                                                self.ownMachineInfo.machineName,
                                                time.time())
        if message:
            result.recordLogMessage(message)
        return result


    def sendHeartbeat(self, testLooperClient, testId, commitId):
        if self.heartbeatResponse != TestResult.TestResult.HEARTBEAT_RESPONSE_ACK:
            logging.info('Machine %s skipping heartbeat because it already received "%s"',
                         self.ownMachineInfo.machineName,
                         self.heartbeatResponse)
            # don't hearbeat again if you already got a response other
            # than ACK
            return

        self.heartbeatResponse = testLooperClient.heartbeat(testId,
                                                            commitId,
                                                            self.ownMachineInfo.machineName)
        if self.heartbeatResponse != TestResult.TestResult.HEARTBEAT_RESPONSE_ACK:
            logging.info(
                "Machine %s is raising TestInterruptException due to heartbeat response: %s",
                self.ownMachineInfo.machineName,
                self.heartbeatResponse
                )
            raise TestInterruptException(self.heartbeatResponse)

        if self.stopEvent.is_set():
            raise TestInterruptException('stop')


    def upload_build(self, commit_id, build_output_dir):
        package_filename = os.path.join(build_output_dir,
                                        self.package_name_for_commit(commit_id))
        if not os.path.exists(package_filename):
            logging.warn("Build output not found: %s", package_filename)
            return False

        self.settings.osInteractions.cache_build(commit_id, package_filename)
        s3_key_name = self.s3_key_name_for_commit(commit_id)
        try:
            logging.info("Uploading build '%s' to %s",
                         package_filename,
                         self.settings.awsConnector.get_build_s3_url(s3_key_name))
            self.settings.awsConnector.upload_build(s3_key_name,
                                                    package_filename)
            return True
        except:
            logging.error("Failed to upload package '%s' to %s\n%s",
                          package_filename,
                          self.settings.awsConnector.get_build_s3_url(s3_key_name),
                          traceback.format_exc())
            return False


    def download_build(self, commit_id, test_dir):
        package_file = self.settings.osInteractions.find_cached_build(commit_id)
        if package_file is None:
            package_file = os.path.join(test_dir,
                                        self.package_name_for_commit(commit_id))
            logging.info("Downloading build to: %s", package_file)
            self.settings.awsConnector.download_build(self.s3_key_name_for_commit(commit_id),
                                                      package_file)
            self.settings.osInteractions.cache_build(commit_id, package_file)
        else:
            logging.info("Using cached build from: %s", package_file)
        return package_file


    def test_env_overrides(self, test, test_output_dir):
        return  {
            'REVISION': test.commitId,
            'OUTPUT_DIR': test_output_dir,
            'CORE_DUMP_DIR': self.settings.coreDumpsDir,
            'UFORA_PERFORMANCE_TEST_RESULTS_FILE': self.perf_test_output_file,
            'TEST_LOOPER_TEST_ID': test.testId,
            'TEST_LOOPER_MULTIBOX_IP_LIST': test.createIpListToPassToScript(),
            'TEST_LOOPER_MULTIBOX_OWN_IP': self.ownMachineInfo.internalIpAddress,
            'AWS_AVAILABILITY_ZONE' : self.ownMachineInfo.availabilityZone
            }


    def runTestUsingScript(self, script, env_overrides, heartbeat, output_dir):
        test_logfile = os.path.join(output_dir, 'test_out.log')
        logging.info("Machine %s is logging to %s",
                     self.ownMachineInfo.machineName,
                     test_logfile)

        success = False
        try:
            success = self.settings.osInteractions.run_command(
                script,
                test_logfile,
                env_overrides,
                self.settings.timeout,
                heartbeat
                )
        except TestInterruptException:
            logging.info("TestInterruptException in machine: %s. Heartbeat response: %s",
                         self.ownMachineInfo.machineName,
                         self.heartbeatResponse)
            if self.stopEvent.is_set():
                return
            success = self.heartbeatResponse == TestResult.TestResult.HEARTBEAT_RESPONSE_DONE

        return success


    def package_name_for_commit(self, commit_id):
        return "ufora-%s.tar.gz" % commit_id


    def s3_key_name_for_commit(self, commit_id):
        return "%s/%s" % (commit_id, self.package_name_for_commit(commit_id))


    def capture_perf_results(self, test_name, perf_output_file, test_result):
        try:
            test_result.recordPerformanceTests(
                self.settings.osInteractions.extractPerformanceTests(perf_output_file,
                                                                     test_name)
                )
        except:
            logging.error(
                "Machine %s failed to read performance test data: %s",
                self.ownMachineInfo.machineName,
                traceback.format_exc()
                )
            test_result.recordLogMessage("Failed to read performance tests")
