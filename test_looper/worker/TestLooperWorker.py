import collections
import logging
import os
import socket
import time
import traceback
import threading
import psutil

import test_looper.core.ManagedThread as ManagedThread
import test_looper.worker.TestLooperClient as TestLooperClient


def kill_proc_tree(pid, including_parent=True):
    parent = psutil.Process(pid)
    children = parent.children(recursive=True)
    for child in children:
        child.kill()

    gone, still_alive = psutil.wait_procs(children, timeout=5)

    if including_parent:
        parent.kill()
        parent.wait(5)


HEARTBEAT_INTERVAL = TestLooperClient.TestLooperClient.HEARTBEAT_INTERVAL


class TestInterruptException(Exception):
    pass


class TestLooperWorker(object):
    def __init__(
        self,
        workerState,
        machineId,
        serverPortConfig,
        exitProcessOnException,
        timeToSleepWhenThereIsNoWork,
    ):
        self.workerState = workerState
        self.machineId = machineId
        self.serverPortConfig = serverPortConfig

        self.timeToSleepWhenThereIsNoWork = timeToSleepWhenThereIsNoWork

        self.stopEvent = threading.Event()

        self.exitProcessOnException = exitProcessOnException

        self.testLooperClient = None

        self.thread = None

    def createTestLooperClient(self):
        return TestLooperClient.TestLooperClient(
            host=self.serverPortConfig.server_address,
            port=self.serverPortConfig.server_worker_port,
            use_ssl=self.serverPortConfig.server_worker_port_use_ssl,
            machineId=self.machineId,
        )

    def stop(self, join=True):
        try:
            logging.info("TestLooperWorker stopping")
            self.stopEvent.set()

            if self.testLooperClient:
                self.testLooperClient.stop()

            if self.thread:
                if join:
                    self.thread.join()
                self.thread = None
        finally:
            logging.info("TestLooperWorker stopped")

    def start(self):
        assert self.thread is None
        self.thread = ManagedThread.ManagedThread(target=self._mainTestLoop)
        self.thread.start()

    def _mainTestLoop(self):
        try:
            self.testLooperClient = self.createTestLooperClient()

            while not self.stopEvent.is_set():
                work = self.testLooperClient.checkoutWork(
                    self.timeToSleepWhenThereIsNoWork
                )
                if work is not None:
                    testOrDeployId, testDefinition, historicalTestFailureRates, isDeploy = (
                        work
                    )
                    self.run_task(
                        testOrDeployId,
                        testDefinition,
                        historicalTestFailureRates,
                        isDeploy,
                    )
        except:
            logging.critical(
                "Unhandled error in TestLooperWorker socket loop:\n%s",
                traceback.format_exc(),
            )
        finally:
            if self.exitProcessOnException:
                logging.info(
                    "Machine %s is exiting the test-looper process.", self.machineId
                )

                kill_proc_tree(os.getpid())
            else:
                logging.info(
                    "Machine %s is exiting the TestLooperWorker but not the process.",
                    self.machineId,
                )

    def run_task(self, testId, testDefinition, historicalTestFailureRates, isDeploy):
        logging.info(
            "Machine %s is working on %s %s, which is %s with hash %s",
            self.machineId,
            "test" if not isDeploy else "deployment",
            testId,
            testDefinition.name,
            testDefinition.hash,
        )

        self.workerState.purge_build_cache()

        result = self.workerState.runTest(
            testId,
            self.testLooperClient,
            testDefinition,
            isDeploy,
            historicalTestFailureRates=historicalTestFailureRates,
        )

        if not self.stopEvent.is_set():
            if isDeploy:
                self.testLooperClient.deploymentExitedEarly()
            else:
                result, individualTestSuccesses = result
                self.testLooperClient.publishTestResult(result, individualTestSuccesses)
