#!/usr/bin/env python

import argparse
import boto
import pprint
import boto.s3.key
import boto.utils
import sys
import json
import multiprocessing
import logging
import signal
import socket
import subprocess
import threading
import time
import os

import test_looper.core.cloud.FromConfig
import test_looper.core.cloud.MachineInfo as MachineInfo
import test_looper.worker.TestLooperClient as TestLooperClient
import test_looper.worker.TestLooperWorker as TestLooperWorker
import test_looper.worker.WorkerState as WorkerState
import test_looper.core.source_control.SourceControlFromConfig as SourceControlFromConfig
import test_looper.core.ArtifactStorage as ArtifactStorage


def createArgumentParser():
    parser = argparse.ArgumentParser()
    parser.add_argument('config',
                        help="Configuration file")
    return parser

def configureLogging(verbose=False):
    if logging.getLogger().handlers:
        logging.getLogger().handlers = []

    loglevel = logging.DEBUG if verbose else logging.INFO
    logging.getLogger().setLevel(loglevel)

    handler = logging.StreamHandler(stream=sys.stderr)

    handler.setLevel(loglevel)
    handler.setFormatter(
        logging.Formatter(
            '%(asctime)s %(levelname)s %(filename)s:%(lineno)s@%(funcName)s %(name)s - %(message)s'
            )
        )
    logging.getLogger().addHandler(handler)


def createTestWorker(config, machineInfo):
    artifactStorage = ArtifactStorage.storageFromConfig(config['artifacts'])

    osInteractions = WorkerState.WorkerState(
        os.path.expandvars(config['worker']['path']), 
        source_control=SourceControlFromConfig.getFromConfig(config["source_control"]),
        artifactStorage=artifactStorage,
        machineInfo=MachineInfo.MachineInfo("localhost",
                                          "localhost",
                                          1,
                                          "none",
                                          "bare metal"
                                          )
        )
    
    def createTestLooperClient():
        return TestLooperClient.TestLooperClient(
            host=config['server']['address'],
            port=config['server']['port']
            )

    workerSettings = TestLooperWorker.TestLooperSettings(
        osInteractions=osInteractions,
        testLooperClientFactory=createTestLooperClient,
        artifactsFileName=config['worker']['test_artifacts'],
        timeout=config['worker']['test_timeout'],
        coreDumpsDir=config['worker']['core_dump_dir'],
        repoName=config['worker']['repo_name']
        )

    return TestLooperWorker.TestLooperWorker(workerSettings, machineInfo)

def loadConfiguration(configFile):
    with open(configFile, 'r') as fin:
        return json.loads(fin.read())

if __name__ == "__main__":
    configureLogging()

    args = createArgumentParser().parse_args()
    config = loadConfiguration(args.config)

    cloud_connection = test_looper.core.cloud.FromConfig.fromConfig(config)
    
    machineInfo = cloud_connection.getOwnMachineInfo()

    logging.info(
        "Starting test-looper on %s with config: %s", 
        machineInfo, 
        pprint.PrettyPrinter().pprint(config)
        )

    testLooperWorker = createTestWorker(config, machineInfo)
    workerThread = threading.Thread(target=testLooperWorker.startTestLoop)

    def handleStopSignal(signum, _):
        logging.info("Signal received: %s. Stopping service.", signum)
        if workerThread and workerThread.isAlive():
            testLooperWorker.stop()
            workerThread.join()

    signal.signal(signal.SIGTERM, handleStopSignal) # handle kill
    signal.signal(signal.SIGINT, handleStopSignal)  # handle ctrl-c

    workerThread.start()
    while workerThread.is_alive():
        time.sleep(0.5)

