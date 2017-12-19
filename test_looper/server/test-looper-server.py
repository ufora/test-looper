#!/usr/bin/env python

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time

import test_looper.core.source_control.SourceControlFromConfig as SourceControlFromConfig
from test_looper.core.RedisJsonStore import RedisJsonStore
from test_looper.core.InMemoryJsonStore import InMemoryJsonStore
import test_looper.server.TestLooperHttpServer as TestLooperHttpServer
from test_looper.server.TestLooperHttpServerEventLog import TestLooperHttpServerEventLog
import test_looper.server.TestLooperServer as TestLooperServer
import test_looper.data_model.TestManager as TestManager
import test_looper.core.ArtifactStorage as ArtifactStorage
import test_looper.core.cloud.FromConfig

def createArgumentParser():
    parser = argparse.ArgumentParser(
        description="Handles test-looper connections and assign test jobs to loopers."
        )

    parser.add_argument('config',
                        help="Path to configuration file")

    parser.add_argument("-v",
                        "--verbose",
                        action='store_true',
                        help="Set logging level to verbose")

    parser.add_argument("--local",
                        action='store_true',
                        help="Run locally without EC2")

    parser.add_argument("--auth",
                        choices=['full', 'write', 'none'],
                        default='full',
                        help=("Authentication requirements.\n"
                              "Full: no unauthenticated access\n"
                              "Write: must authenticate to write\n"
                              "None: open, unauthenticated access"))

    return parser

def loadConfiguration(configFile):
    with open(configFile, 'r') as fin:
        return json.loads(fin.read())

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

def main():
    parsedArgs = createArgumentParser().parse_args()
    config = loadConfiguration(parsedArgs.config)
    configureLogging(verbose=parsedArgs.verbose)
    
    port = config['server']['worker_port']
    logging.info("Starting test-looper server on port %d", port)

    src_ctrl = SourceControlFromConfig.getFromConfig(config["source_control"])

    jsonStore = RedisJsonStore(port=config['server'].get('redis_port'))
    #jsonStore = InMemoryJsonStore()

    testManager = TestManager.TestManager(
        src_ctrl,
        jsonStore,
        TestManager.TestManagerSettings.Settings(
            max_test_count=config['server'].get('max_test_count', 3)
            )
        )

    http_port = config['server'].get('http_port', 80)

    cloud_connection = test_looper.core.cloud.FromConfig.fromConfig(config)
    
    httpServer = TestLooperHttpServer.TestLooperHttpServer(
        config['server']['web_address'],
        testManager,
        cloud_connection,
        ArtifactStorage.storageFromConfig(config['artifacts']),
        src_ctrl,
        event_log=TestLooperHttpServerEventLog(jsonStore),
        auth_level=parsedArgs.auth,
        httpPort=http_port,
        enable_advanced_views=config['server'].get('enable_advanced_views', False),
        wetty_port=config['server']['wetty_port'],
        certs=config['server'].get('certs')
        )

    server = TestLooperServer.TestLooperServer(port,
                                               testManager,
                                               httpServer,
                                               cloud_connection
                                               )

    serverThread = threading.Thread(target=server.runListenLoop)
    def handleStopSignal(signum, _):
        logging.info("Signal received: %s. Stopping service.", signum)
        if serverThread and serverThread.isAlive():
            server.stop()
        logging.info("Stopping service complete.")
        os._exit(0)

    signal.signal(signal.SIGTERM, handleStopSignal) # handle kill
    signal.signal(signal.SIGINT, handleStopSignal)  # handle ctrl-c

    serverThread.start()
    while serverThread.is_alive():
        time.sleep(0.5)

if __name__ == "__main__":
    main()
