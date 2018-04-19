#!/usr/bin/env python

import argparse
import pprint
import sys
import json
import logging
import signal
import threading
import time
import os
import multiprocessing
import psutil

import test_looper.core.algebraic_to_json as algebraic_to_json
import test_looper.core.Config as Config
import test_looper.worker.TestLooperClient as TestLooperClient
import test_looper.worker.TestLooperWorker as TestLooperWorker
import test_looper.worker.WorkerState as WorkerState
import test_looper.core.source_control.SourceControlFromConfig as SourceControlFromConfig
import test_looper.core.ArtifactStorage as ArtifactStorage


def createArgumentParser():
    parser = argparse.ArgumentParser()
    parser.add_argument('config',
                        help="Configuration file")
    parser.add_argument('machineId',
                        type=str,
                        help="Number of workers to run")
    parser.add_argument('worker_path',
                        type=str,
                        help="Path to storage we can use")
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


def createTestWorker(config, worker_path, machineId):
    config = algebraic_to_json.Encoder().from_json(config, Config.WorkerConfig)
    
    source_control = SourceControlFromConfig.getFromConfig(os.path.join(worker_path,"worker_repo_cache"), config.source_control)
    artifact_storage = ArtifactStorage.storageFromConfig(config.artifacts)
    
    workerState = WorkerState.WorkerState(
        name_prefix="test_looper_worker",
        worker_directory=worker_path,
        source_control=source_control,
        artifactStorage=artifact_storage,
        machineId=machineId,
        hardwareConfig=Config.HardwareConfig(
            cores=multiprocessing.cpu_count(),
            ram_gb=int(psutil.virtual_memory().total / 1024.0 / 1024.0 / 1024.0 + .1)
            ),
        docker_image_repo=config.server_ports.docker_image_repo
        )

    return TestLooperWorker.TestLooperWorker(workerState, machineId, config.server_ports, True, 2.0)

def loadConfiguration(configFile):
    with open(configFile, 'r') as fin:
        return json.loads(fin.read())

if __name__ == "__main__":
    configureLogging()

    args = createArgumentParser().parse_args()
    config = loadConfiguration(args.config)

    logging.info(
        "Starting test-looper on %s with and config\n%s", 
        "machineId", 
        pprint.PrettyPrinter().pformat(config)
        )

    testLooperWorker = createTestWorker(config, args.worker_path, args.machineId)

    testLooperWorker.start()

    try:
        while True:
            testLooperWorker.thread.join(.1)
    except KeyboardInterrupt:
        logging.info("Stopping worker thread and exiting on keyboard interrupt.")
        testLooperWorker.stop()

