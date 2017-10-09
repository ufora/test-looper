#!/usr/bin/env python

import argparse
import boto
import boto.s3.key
import boto.utils
import json
import multiprocessing
import logging
import signal
import socket
import subprocess
import threading
import time
import os

import test_looper.worker.TestLooperClient as TestLooperClient
import test_looper.worker.TestLooperWorker as TestLooperWorker
import test_looper.worker.TestLooperOsInteractions as TestLooperOsInteractions
import test_looper.server.source_control.SourceControlFromConfig as SourceControlFromConfig
import test_looper.core.ArtifactStorage as ArtifactStorage


def createArgumentParser():
    parser = argparse.ArgumentParser()
    parser.add_argument('config',
                        help="Configuration file")
    return parser

def initLogging():
    logging.getLogger().setLevel(logging.INFO)
    ch = logging.StreamHandler()
    ch.setFormatter(
        logging.Formatter(
            '%(asctime)s %(levelname)s %(filename)s:%(lineno)s@%(funcName)s %(name)s - %(message)s'
            )
        )
    logging.getLogger().addHandler(ch)


def createTestWorker(config, testLooperMachineInfo):
    directories = TestLooperOsInteractions.TestLooperDirectories(
        repo_dir=config['worker']['working_repo'],
        test_data_dir=config['worker']['test_data_dir'],
        build_cache_dir=config['worker']['build_cache_dir'],
        ccache_dir=config['worker']['ccache_dir']
        )
    osInteractions = TestLooperOsInteractions.TestLooperOsInteractions(
        directories, 
        SourceControlFromConfig.getFromConfig(config["source_control"])
        )
    osInteractions.initializeTestLooperEnvironment()

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
        artifactStorage=ArtifactStorage.storageFromConfig(config),
        coreDumpsDir=config['worker']['core_dump_dir'],
        repoName=config['worker']['repo_name']
        )

    return TestLooperWorker.TestLooperWorker(workerSettings, testLooperMachineInfo)

def getMachineInfo():
    ownMachineName = None
    ownInternalIpAddress = None
    availabilityZone = ''
    instanceType = 'local.machine'

    metadata = boto.utils.get_instance_metadata(timeout=2.0, num_retries=1)
    if metadata:
        ownInternalIpAddress = metadata.get('local-ipv4') or ownInternalIpAddress

        ownMachineName = metadata.get('public-hostname') or \
                         metadata.get('public-ipv4') or \
                         ownInternalIpAddress

        availabilityZone = metadata.get('placement', {}).get('availability-zone') or \
                           availabilityZone
        logging.info("Resolved availabilityZone: %s", availabilityZone)

        instanceType = metadata.get('instance-type')
    else:
        ownMachineName = socket.gethostname()
        ownInternalIpAddress = socket.gethostbyname(socket.gethostname())

    ownCoreCount = multiprocessing.cpu_count()

    return TestLooperWorker.TestLooperMachineInfo(ownMachineName,
                                                  ownInternalIpAddress,
                                                  ownCoreCount,
                                                  availabilityZone,
                                                  instanceType)

def loadConfiguration(configFile):
    with open(configFile, 'r') as fin:
        return json.loads(fin.read())

if __name__ == "__main__":
    initLogging()

    args = createArgumentParser().parse_args()
    config = loadConfiguration(args.config)
    machineInfo = getMachineInfo()
    logging.info("Starting test-looper on %s with config: %s", machineInfo, config)

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

