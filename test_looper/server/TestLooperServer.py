import collections
import json
import logging
import threading
import traceback
import base64
import time

import test_looper.core.SimpleServer as SimpleServer
import test_looper.core.socket_util as socket_util
import test_looper.core.algebraic as algebraic
import test_looper.core.algebraic_to_json as algebraic_to_json

SWEEP_FREQUENCY = 30

TerminalInputMsg = algebraic.Alternative("TerminalInputMsg")
TerminalInputMsg.KeyboardInput = {"bytes": str}
TerminalInputMsg.Resize = {"cols": int, "rows": int}

ServerToClientMsg = algebraic.Alternative("ServerToClientMsg")
ServerToClientMsg.TerminalInput = {'deploymentId': str, 'msg': TerminalInputMsg}
ServerToClientMsg.TestAssignment = {'repoName': str, 'commitHash': str, 'testId': str, 'testName': str}
ServerToClientMsg.CancelTest = {'testId': str}

ServerToClientMsg.DeploymentAssignment = {'repoName': str, 'commitHash': str, 'deploymentId': str, 'testName': str}
ServerToClientMsg.ShutdownDeployment = {'deploymentId': str}

ClientToServerMsg = algebraic.Alternative("ClientToServerMsg")

ClientToServerMsg.InitializeConnection = {'machineId': str}
ClientToServerMsg.WaitingHeartbeat = {}
ClientToServerMsg.TestHeartbeat = {'testId': str}
ClientToServerMsg.TestLogOutput = {'testId': str, 'log': str}
ClientToServerMsg.DeploymentHeartbeat = {'deploymentId': str}
ClientToServerMsg.DeploymentTerminalOutput = {'deploymentId': str, 'data': str}
ClientToServerMsg.TestFinished = {'testId': str, 'success': bool, 'testSuccesses': algebraic.Dict(str,bool)}


class Session(object):
    def __init__(self, testManager, machine_management, socket, address):
        self.socket = socket
        self.address = address
        self.testManager = testManager
        self.machine_management = machine_management
        self.currentTestId = None
        self.currentDeploymentId = None
        self.socketLock = threading.Lock()
        self.machineId = None

        logging.info("Incoming Server Connection initialized.")

    def __call__(self):
        try:
            while True:
                msg = algebraic_to_json.Encoder().from_json(
                    json.loads(self.readString()),
                    ClientToServerMsg
                    )
                self.processMsg(msg)
        except socket_util.SocketException as e:
            logging.info("Socket error: %s", e.message)
        except:
            logging.error("Exception: %s", traceback.format_exc())
        finally:
            self.socket.close()

    def send(self, msg):
        self.writeString(json.dumps(algebraic_to_json.Encoder().to_json(msg)))

    def processMsg(self, msg):
        if msg.matches.InitializeConnection:
            self.machineId = msg.machineId
            self.testManager.machineInitialized(msg.machineId, time.time())
        elif msg.matches.WaitingHeartbeat:
            self.testManager.machineHeartbeat(self.machineId, time.time())
            if self.currentDeploymentId is None and self.currentTestId is None:
                repoName, commitHash, testName, deploymentId = self.testManager.startNewDeployment(self.machineId, time.time())
                if repoName is not None:
                    self.currentDeploymentId = deploymentId
                    self.send(ServerToClientMsg.DeploymentAssignment(
                        repoName=repoName,
                        commitHash=commitHash,
                        testName=testName,
                        deploymentId=deploymentId
                        ))
                    def onMessage(msg):
                        if self.currentDeploymentId == deploymentId:
                            self.send(ServerToClientMsg.TerminalInput(deploymentId=deploymentId,msg=msg))
                    self.testManager.subscribeToClientMessages(deploymentId, onMessage)
                else:
                    repoName, commitHash, testName, testId = self.testManager.startNewTest(self.machineId, time.time())
                    if repoName is not None:
                        self.currentTestId = testId
                        self.send(ServerToClientMsg.TestAssignment(
                            repoName=repoName,
                            commitHash=commitHash,
                            testName=testName,
                            testId=testId
                            ))

        elif msg.matches.TestHeartbeat or msg.matches.TestLogOutput:
            if msg.matches.TestHeartbeat:
                log = None
            else:
                log = msg.log

            if msg.testId == self.currentTestId:
                if not self.testManager.testHeartbeat(msg.testId, time.time(), log):
                    logging.info("Server canceling test %s on machine %s", msg.testId, self.machineId)

                    self.send(ServerToClientMsg.CancelTest(testId=msg.testId))
                    self.currentTestId = None
        elif msg.matches.DeploymentHeartbeat or msg.matches.DeploymentTerminalOutput:
            log = msg.data if msg.matches.DeploymentTerminalOutput else None
            if msg.deploymentId == self.currentDeploymentId:
                if not self.testManager.handleMessageFromDeployment(msg.deploymentId, time.time(), log):
                    self.send(ServerToClientMsg.ShutdownDeployment(msg.deploymentId))
                    self.currentDeploymentId = None
        elif msg.matches.TestFinished:
            self.testManager.recordTestResults(msg.success, msg.testId, msg.testSuccesses, time.time())
            self.currentTestId = None

    def readString(self):
        return socket_util.readString(self.socket)

    def writeString(self, s):
        with self.socketLock:
            return socket_util.writeString(self.socket, s)

class TestLooperServer(SimpleServer.SimpleServer):
    #if we modify this protocol version, the loopers should reboot and pull a new copy of the code
    protocolVersion = '2.2.6'

    def __init__(self, server_ports, testManager, httpServer, machine_management):
        """
        Initialize a TestLooperServer
        """
        if httpServer.certs is not None:
            cert_and_keyfile = (httpServer.certs.cert, httpServer.certs.key)
        else:
            cert_and_keyfile = None

        SimpleServer.SimpleServer.__init__(self, server_ports.server_worker_port, cert_and_key_paths = cert_and_keyfile)

        self.port_ = server_ports.server_worker_port
        self.testManager = testManager
        self.httpServer = httpServer
        self.machine_management = machine_management
        self.workerThread = threading.Thread(target=self.executeManagerWork)
        self.workerThread.daemon=True

    def executeManagerWork(self):
        try:
            lastSweep = None

            while not self.shouldStop():
                task = self.testManager.performBackgroundWork(time.time())

                if lastSweep is None or time.time() - lastSweep > SWEEP_FREQUENCY:
                    lastSweep = time.time()
                    try:
                        self.testManager.performCleanupTasks(time.time())
                    except:
                        logging.critical("Test manager failed during cleanup:\n%s", traceback.format_exc())

                if task:
                    logging.info("Performed %s", task)
                if task is None:
                    time.sleep(.1)
        except:
            logging.critical("Manager worker thread exiting:\n%s", traceback.format_exc())

    def port(self):
        return self.port_

    def initialize(self):
        logging.info("Initializing TestManager.")
        self.testManager.markRepoListDirty(time.time())
        self.testManager.pruneDeadWorkers(time.time())
        logging.info("DONE Initializing TestManager.")
        

    def runListenLoop(self):
        logging.info("Starting TestLooperServer listen loop")

        self.httpServer.start()
        self.workerThread.start()

        logging.info("HTTP server started")

        try:
            self.initialize()
            logging.info("TestLooper initialized")

            super(TestLooperServer, self).runListenLoop()
        finally:
            self.httpServer.stop()
            logging.info("Listen loop stopped")

    def stop(self):
        super(TestLooperServer, self).stop()
        
        logging.info("waiting for worker thread...")

        #self.workerThread.join()

        #logging.info("successfully stopped TestLooperServer")

    def _onConnect(self, socket, address):
        logging.debug("Accepting connection from %s", address)
        threading.Thread(target=Session(self.testManager,
                                        self.machine_management,
                                        socket,
                                        address)).start()
