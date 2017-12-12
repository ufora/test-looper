import collections
import logging
import os
import socket
import time
import traceback
import threading

import test_looper.data_model.TestResult as TestResult
import test_looper.worker.TestLooperClient as TestLooperClient

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
        if self.testLooperClient:
            self.testLooperClient.stop()

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
                    return
                    
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
        self.heartbeatResponse = TestResult.HEARTBEAT_RESPONSE_ACK
        self.testLooperClient = self.settings.testLooperClientFactory()

        commit_and_test = self.testLooperClient.getTask(self.ownMachineInfo)

        if commit_and_test is None:
            return self.timeToSleepWhenThereIsNoWork

        self.run_task(
            commit_and_test["repoName"], 
            commit_and_test["commitHash"],
            commit_and_test["testId"],
            commit_and_test["testName"]
            )

        return 0


    def run_task(self, repoName, commitHash, testId, testName):
        logging.info("Machine %s is working on testId %s, test %s/%s, for commit %s",
                     self.ownMachineInfo.machineId,
                     testId,
                     testName,
                     repoName, 
                     commitHash
                     )

        def heartbeat():
            return self.sendHeartbeat(self.testLooperClient, testId, repoName, commitHash)

        result = self.settings.osInteractions.runTest(testId, repoName, commitHash, testName, heartbeat)
        
        if not self.stopEvent.is_set():
            self.testLooperClient.publishTestResult(result)

    def sendHeartbeat(self, testLooperClient, testId, repoName, commitHash):
        if self.heartbeatResponse != TestResult.HEARTBEAT_RESPONSE_ACK:
            logging.info('Machine %s skipping heartbeat because it already received "%s"',
                         self.ownMachineInfo.machineId,
                         self.heartbeatResponse)
            return

        self.heartbeatResponse = testLooperClient.heartbeat(testId,
                                                            repoName, 
                                                            commitHash,
                                                            self.ownMachineInfo.machineId)

        if self.heartbeatResponse != TestResult.HEARTBEAT_RESPONSE_ACK:
            logging.info(
                "Machine %s is raising TestInterruptException due to heartbeat response: %s",
                self.ownMachineInfo.machineId,
                self.heartbeatResponse
                )
            raise TestInterruptException(self.heartbeatResponse)

        if self.stopEvent.is_set():
            raise TestInterruptException('stop')

