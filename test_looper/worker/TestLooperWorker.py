import collections
import logging
import os
import socket
import time
import traceback
import threading

import test_looper.core.ManagedThread as ManagedThread
import test_looper.data_model.TestResult as TestResult
import test_looper.worker.TestLooperClient as TestLooperClient

HEARTBEAT_INTERVAL = TestLooperClient.TestLooperClient.HEARTBEAT_INTERVAL

class TestInterruptException(Exception):
    pass

class TestLooperWorker(object):
    def __init__(self,
                 workerState,
                 machineId,
                 serverPortConfig,
                 timeToSleepWhenThereIsNoWork=2.0
                ):
        self.workerState = workerState
        self.machineId = machineId
        self.serverPortConfig = serverPortConfig

        self.timeToSleepWhenThereIsNoWork = timeToSleepWhenThereIsNoWork

        self.stopEvent = threading.Event()

        self.heartbeatResponse = None
        
        self.testLooperClient = None

        self.thread = None

    def createTestLooperClient(self):
        return TestLooperClient.TestLooperClient(
            host=self.serverPortConfig.server_address,
            port=self.serverPortConfig.server_worker_port,
            use_ssl=self.serverPortConfig.server_worker_port_use_ssl
            )

    def stop(self, join=True):
        self.stopEvent.set()
        if self.testLooperClient:
            self.testLooperClient.stop()
        if self.thread:
            if join:
                self.thread.join()
            self.thread = None

    def start(self):
        assert self.thread is None
        self.thread = ManagedThread.ManagedThread(target=self._mainTestLoop)
        self.thread.start()

    def _mainTestLoop(self):
        try:
            socketErrorCount = 0
            waitTime = 0
            errorsInARow = 0
            while not self.stopEvent.is_set():
                try:
                    waitTime = self._tryToRunOneTest()
                    socketErrorCount = 0
                    errorsInARow = 0
                except TestLooperClient.ProtocolMismatchException:
                    logging.info("protocol mismatch observed on %s: %s",
                                 self.machineId,
                                 traceback.format_exc())
                    return
                    
                except socket.error:
                    logging.info("Can't connect to server")
                    socketErrorCount += 1
                    errorsInARow += 1
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
                        self.machineId,
                        errorsInARow,
                        waitTime,
                        traceback.format_exc()
                        )

                if waitTime > 0:
                    self.stopEvent.wait(waitTime)

        finally:
            logging.info("Machine %s is exiting main testing loop",
                         self.machineId)


    def _tryToRunOneTest(self):
        self.heartbeatResponse = TestResult.HEARTBEAT_RESPONSE_ACK

        if self.testLooperClient is None:
            self.testLooperClient = self.createTestLooperClient()

        commit_and_test = self.testLooperClient.getTask(self.machineId)

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
                     self.machineId,
                     testId,
                     testName,
                     repoName, 
                     commitHash
                     )

        def heartbeat(logMessage=None):
            return self.sendHeartbeat(self.testLooperClient, testId, repoName, commitHash, logMessage)

        result = self.workerState.runTest(testId, repoName, commitHash, testName, heartbeat)
        
        if not self.stopEvent.is_set():
            self.testLooperClient.publishTestResult(result)

    def sendHeartbeat(self, testLooperClient, testId, repoName, commitHash, logMessage):
        if self.heartbeatResponse != TestResult.HEARTBEAT_RESPONSE_ACK:
            logging.info('Machine %s skipping heartbeat because it already received "%s"',
                         self.machineId,
                         self.heartbeatResponse)
            return

        if self.stopEvent.is_set():
            raise TestInterruptException('stop')

        self.heartbeatResponse = testLooperClient.heartbeat(testId,
                                                            repoName, 
                                                            commitHash,
                                                            self.machineId,
                                                            logMessage
                                                            )

        if self.heartbeatResponse != TestResult.HEARTBEAT_RESPONSE_ACK:
            logging.info(
                "Machine %s is raising TestInterruptException due to heartbeat response: %s",
                self.machineId,
                self.heartbeatResponse
                )
            raise TestInterruptException(self.heartbeatResponse)

