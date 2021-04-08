#!/usr/bin/env python

import argparse
import json
import traceback
import logging
import os
import signal
import socket
import sys
import threading
import time
import yaml
import shutil
import test_looper.core.algebraic_to_json as algebraic_to_json
import test_looper.core.Config as Config
import test_looper.core.machine_management.MachineManagement as MachineManagement

import test_looper.core.source_control.SourceControlFromConfig as SourceControlFromConfig
from test_looper.core.RedisJsonStore import RedisJsonStore
from test_looper.core.InMemoryJsonStore import InMemoryJsonStore
import test_looper.server.TestLooperHttpServer as TestLooperHttpServer
from test_looper.server.TestLooperHttpServerEventLog import TestLooperHttpServerEventLog
import test_looper.server.TestLooperServer as TestLooperServer
import test_looper.data_model.TestManager as TestManager
import test_looper.data_model.ImportExport as ImportExport
import test_looper.core.ArtifactStorage as ArtifactStorage

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

    parser.add_argument("--repocheck",
                        action='store_true',
                        help="Print the set of known repos and exit")

    parser.add_argument("--flush_tasks",
                        action='store_true',
                        help="Flush all tasks before starting the server.")

    parser.add_argument("--export",
                        default=None,
                        help="Export the state of the server to a file in this directory in the background."
                        )

    parser.add_argument("--import",
                        default=None,
                        dest="import_filename",
                        help="Import the state of the server from a file and exit."
                        )

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
        expanded = os.path.expandvars(fin.read())
        return json.loads(expanded)

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

def exportToFile(testManager, exportDir):
    exportPath = os.path.join(exportDir, time.strftime("%Y%m%d-%H%M%S-UTC.yml", time.gmtime()))

    exporter = ImportExport.ImportExport(testManager)
    res = exporter.export()

    logging.info("Dumping yaml file to %s", exportPath)
    with open(exportPath, "w") as f:
        print(yaml.dump(res), file=f)
    logging.info("Done dumping yaml file to %s", exportPath)

def main():
    parsedArgs = createArgumentParser().parse_args()
    config = loadConfiguration(parsedArgs.config)
    configureLogging(verbose=parsedArgs.verbose)

    config = algebraic_to_json.Encoder(mergeListsIntoDicts=False).from_json(config, Config.Config)

    if config.server.database.matches.InMemory:
        jsonStore = InMemoryJsonStore()
    else:
        jsonStore = RedisJsonStore(
            port=config.server.database.port or None, 
            db=config.server.database.db
            )

    eventLog = TestLooperHttpServerEventLog(jsonStore)

    src_ctrl = SourceControlFromConfig.getFromConfig(config.server.path_to_local_repos, config.source_control)

    if parsedArgs.repocheck:
        print("repos: ")
        for r in sorted(src_ctrl.listRepos()):
            print("\t", r, src_ctrl.isWebhookInstalled(r, config.server_ports))
        sys.exit(0)

    artifact_storage = ArtifactStorage.storageFromConfig(config.artifacts)

    artifact_storage.tempfileOverrideDir = os.path.join(config.server.path_to_local_repos, "tarballs")
    if os.path.exists(artifact_storage.tempfileOverrideDir):
        try:
            shutil.rmtree(artifact_storage.tempfileOverrideDir)
        except:
            traceback.print_exc()

    try:
        os.makedirs(artifact_storage.tempfileOverrideDir)
    except:
        traceback.print_exc()

    machine_management = MachineManagement.fromConfig(config, src_ctrl, artifact_storage)

    testManager = TestManager.TestManager(config.server_ports, src_ctrl, machine_management, jsonStore)

    if parsedArgs.export:
        exportToFile(testManager, parsedArgs.export)
        sys.exit(0)

    if parsedArgs.import_filename:
        logging.info("Loading yaml file: %s", parsedArgs.import_filename)
        with open(parsedArgs.import_filename, "r") as f:
            res = yaml.load(f.read())
        logging.info("Done loading yaml file: %s", parsedArgs.import_filename)

        exporter = ImportExport.ImportExport(testManager)

        errors = exporter.importResults(res)

        print("burning down background work")
        while testManager.performBackgroundWorkSynchronously(time.time(), 100):
            pass

        if errors:
            print("*****************")
            print("import errors: ")
            for e in errors:
                print("\t", e)

            sys.exit(1)
        else:
            print("imported successfully")

            sys.exit(0)
    
    if parsedArgs.flush_tasks:
        print("Before booting, cleaning up old tasks...")
        while testManager.performBackgroundWorkSynchronously(time.time(), 100):
            pass

    
    httpServer = TestLooperHttpServer.TestLooperHttpServer(
        config.server_ports,
        config.http_server,
        config.server,
        testManager,
        machine_management,
        artifact_storage,
        src_ctrl,
        event_log=eventLog
        )

    server = TestLooperServer.TestLooperServer(config.server_ports,
                                               testManager,
                                               httpServer,
                                               machine_management
                                               )

    serverThread = threading.Thread(target=server.runListenLoop)

    def handleStopSignal(signum, _):
        logging.info("Signal received: %s. Stopping service.", signum)
        os._exit(0)

    signal.signal(signal.SIGTERM, handleStopSignal) # handle kill
    signal.signal(signal.SIGINT, handleStopSignal)  # handle ctrl-c

    serverThread.start()

    while True:
        time.sleep(1.0)

if __name__ == "__main__":
    main()
