import collections
import logging
import os
import socket
import time
import traceback
import threading

import test_looper.data_model.TestResult as TestResult
import test_looper.worker.TestLooperClient as TestLooperClient
import test_looper.data_model.TestScriptDefinition as TestScriptDefinition

HEARTBEAT_INTERVAL = TestLooperClient.TestLooperClient.HEARTBEAT_INTERVAL

class TestInterruptException(Exception):
    pass

TestLooperSettings = collections.namedtuple(
    'TestLooperSettings',
    [
        'osInteractions',
        'testLooperClientFactory',
        'timeout',
        'coreDumpsDir',
        'repoName'
    ])

class TestLooperWorker(object):
    perf_test_output_file = 'performanceMeasurements.json'

    def __init__(self,
                 testLooperSettings,
                 machineInfo,
                 timeToSleepWhenThereIsNoWork=2.0
                ):
        self.settings = testLooperSettings
        self.ownMachineInfo = machineInfo
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
            errorsInARow = 0
            while not self.stopEvent.is_set():
                try:
                    waitTime = self.mainTestingIteration()
                    socketErrorCount = 0
                    errorsInARow = 0
                except TestLooperClient.ProtocolMismatchException:
                    logging.info("protocol mismatch observed on %s: %s",
                                 self.ownMachineInfo.machineId,
                                 traceback.format_exc())
                    return self.protocolMismatchObserved()
                except socket.error:
                    logging.info("Can't connect to server")
                    socketErrorCount += 1
                    errorsInARow += 1
                    if socketErrorCount > 24:
                        return self.settings.osInteractions.abortTestLooper(
                            "Unable to communicate with server."
                            )
                    waitTime = 5.0
                except Exception as e:
                    errorsInARow += 1
                    if errorsInARow < 5:
                        waitTime = 1.0
                    else:
                        waitTime = 10.0

                    logging.error(
                        "Exception %s on %s. errorsInARow==%s. Waiting for %s and trying again.: %s.",
                        type(e),
                        self.ownMachineInfo.machineId,
                        errorsInARow,
                        waitTime,
                        traceback.format_exc()
                        )

                if waitTime > 0:
                    self.stopEvent.wait(waitTime)

        finally:
            logging.info("Machine %s is exiting main testing loop",
                         self.ownMachineInfo.machineId)


    def mainTestingIteration(self):
        logging.info("Machine %s is starting a new test loop iteration",
                     self.ownMachineInfo.machineId)
        self.heartbeatResponse = TestResult.TestResult.HEARTBEAT_RESPONSE_ACK
        self.testLooperClient = self.settings.testLooperClientFactory()

        commit_and_test = self.testLooperClient.getTask(self.ownMachineInfo)

        if commit_and_test is None:
            logging.info("Machine %s has nothing to do. Waiting.",
                         self.ownMachineInfo.machineId)
            return self.timeToSleepWhenThereIsNoWork

        logging.info("Machine %s is starting task %s",
                     self.ownMachineInfo.machineId,
                     commit_and_test
                     )

        self.run_task(
            commit_and_test["commitId"], 
            commit_and_test["testId"],
            commit_and_test["testName"]
            )

        return 0


    def run_task(self, commitId, testId, testName):
        logging.info("Machine %s is working on testId %s, test %s, for commit %s",
                     self.ownMachineInfo.machineId,
                     testId,
                     testName,
                     commitId
                     )

        def heartbeat():
            return self.sendHeartbeat(self.testLooperClient, testId, commitId)

        result = self.settings.osInteractions.runTest(testId, commitId, testName, heartbeat)
        
        self.testLooperClient.publishTestResult(result)

    def sendHeartbeat(self, testLooperClient, testId, commitId):
        if self.heartbeatResponse != TestResult.TestResult.HEARTBEAT_RESPONSE_ACK:
            logging.info('Machine %s skipping heartbeat because it already received "%s"',
                         self.ownMachineInfo.machineId,
                         self.heartbeatResponse)
            # don't hearbeat again if you already got a response other
            # than ACK
            return

        self.heartbeatResponse = testLooperClient.heartbeat(testId,
                                                            commitId,
                                                            self.ownMachineInfo.machineId)
        if self.heartbeatResponse != TestResult.TestResult.HEARTBEAT_RESPONSE_ACK:
            logging.info(
                "Machine %s is raising TestInterruptException due to heartbeat response: %s",
                self.ownMachineInfo.machineId,
                self.heartbeatResponse
                )
            raise TestInterruptException(self.heartbeatResponse)

        if self.stopEvent.is_set():
            raise TestInterruptException('stop')


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

