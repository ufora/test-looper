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
        
        self.testLooperClient = self.createTestLooperClient()

        self.thread = None

    def createTestLooperClient(self):
        return TestLooperClient.TestLooperClient(
            host=self.serverPortConfig.server_address,
            port=self.serverPortConfig.server_worker_port,
            use_ssl=self.serverPortConfig.server_worker_port_use_ssl,
            machineId=self.machineId
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
            while not self.stopEvent.is_set():
                work = self.testLooperClient.checkoutWork(self.timeToSleepWhenThereIsNoWork)
                if work is not None:
                    repoName, commitHash, testName, testOrDeployId, isDeploy = work
                    self.run_task(repoName, commitHash, testOrDeployId, testName, isDeploy)
        except:
            logging.critical("Unhandled error in TestLooperWorker socket loop:\n%s", traceback.format_exc())
        finally:
            logging.info("Machine %s is exiting main testing loop",
                         self.machineId)


    def run_task(self, repoName, commitHash, testId, testName, isDeploy):
        logging.info("Machine %s is working on %s %s, test %s/%s, for commit %s",
                     self.machineId,
                     "test" if not isDeploy else "deployment",
                     testId,
                     testName,
                     repoName, 
                     commitHash
                     )

        result = self.workerState.runTest(testId, repoName, commitHash, testName, self.testLooperClient, isDeploy)
        
        if not self.stopEvent.is_set() and not isDeploy:
            self.testLooperClient.publishTestResult(result)
