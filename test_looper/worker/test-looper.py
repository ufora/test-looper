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
import test_looper.core.tools.Git as Git
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
    parser.add_argument('worker_count',
                        type=int,
                        default=1,
                        help="Number of workers to run")
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


def createTestWorker(config, machineInfo, worker_index):
    artifactStorage = ArtifactStorage.storageFromConfig(config['artifacts'])

    source_control = SourceControlFromConfig.getFromConfig(config["source_control"])

    worker_path = str(os.path.join(os.path.expandvars(config['worker']['path']), worker_index))

    osInteractions = WorkerState.WorkerState(
        config['worker'].get('scope',"test_looper") + "_" + worker_index + "_",
        worker_path,
        source_control,
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
        "Starting test-looper on %s with %s workers and config\n%s", 
        machineInfo, 
        args.worker_count,
        pprint.PrettyPrinter().pformat(config)
        )

    testLooperWorkers = [createTestWorker(config, machineInfo, str(ix)) for ix in xrange(args.worker_count)]
    workerThreads = [threading.Thread(target=w.startTestLoop) for w in testLooperWorkers]

    def handleStopSignal(signum, _):
        logging.info("Signal received: %s. Stopping service.", signum)
        for w in testLooperWorkers:
            w.stop()

        logging.info("Waiting for workers to shut down.")

        for thread in workerThreads:
            thread.join()

    signal.signal(signal.SIGTERM, handleStopSignal) # handle kill
    signal.signal(signal.SIGINT, handleStopSignal)  # handle ctrl-c

    for w in workerThreads:
        w.start()

    while [w for w in workerThreads if w.is_alive()]:
        time.sleep(0.1)

