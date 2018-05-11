import collections
import json
import logging
import threading
import traceback
import base64
import time
import random
import socket

import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.core.SimpleServer as SimpleServer
import test_looper.core.socket_util as socket_util
import test_looper.core.algebraic as algebraic
import test_looper.core.algebraic_to_json as algebraic_to_json

CLEANUP_TASK_FREQUENCY = 30

TerminalInputMsg = algebraic.Alternative("TerminalInputMsg")
TerminalInputMsg.KeyboardInput = {"bytes": str}
TerminalInputMsg.Resize = {"cols": int, "rows": int}

ServerToClientMsg = algebraic.Alternative("ServerToClientMsg")
ServerToClientMsg.IdentifyCurrentState = {}
ServerToClientMsg.TerminalInput = {'deploymentId': str, 'msg': TerminalInputMsg}
ServerToClientMsg.TestAssignment = {'testId': str, 'testDefinition': TestDefinition.TestDefinition }
ServerToClientMsg.CancelTest = {'testId': str}
ServerToClientMsg.AcknowledgeFinishedTest = {'testId': str}

ServerToClientMsg.DeploymentAssignment = {'deploymentId': str, 'testDefinition': TestDefinition.TestDefinition }
ServerToClientMsg.ShutdownDeployment = {'deploymentId': str}

ServerToClientMsg.GrantOrDenyPermissionToHitGitRepo = {'requestUniqueId': str, "allowed": bool}

ClientToServerMsg = algebraic.Alternative("ClientToServerMsg")


WorkerState = algebraic.Alternative("WorkerState")
WorkerState.Waiting = {}
WorkerState.WorkingOnDeployment = {'deploymentId': str, 'logs_so_far': str}
WorkerState.WorkingOnTest = {'testId': str, 'logs_so_far': str, 'artifacts': algebraic.List(str)}
WorkerState.TestFinished = {'testId': str, 'success': bool, 'testSuccesses': algebraic.Dict(str,(bool, bool)), 'artifacts': algebraic.List(str)} #testSuccess: name->(success,hasLogs)

ClientToServerMsg.CurrentState = {'machineId': str, 'state': WorkerState}
ClientToServerMsg.WaitingHeartbeat = {}
ClientToServerMsg.TestHeartbeat = {'testId': str}
ClientToServerMsg.ArtifactUploaded = {'testId': str, 'artifact': str}
ClientToServerMsg.TestLogOutput = {'testId': str, 'log': str}
ClientToServerMsg.DeploymentHeartbeat = {'deploymentId': str}
ClientToServerMsg.DeploymentExited = {'deploymentId': str}
ClientToServerMsg.DeploymentTerminalOutput = {'deploymentId': str, 'data': str}
ClientToServerMsg.TestFinished = {'testId': str, 'success': bool, 'testSuccesses': algebraic.Dict(str,(bool, bool)), 'artifacts': algebraic.List(str)} #testSuccess: name->(success,hasLogs)
ClientToServerMsg.RequestPermissionToHitGitRepo = {'requestUniqueId': str, 'curTestOrDeployId': str}
ClientToServerMsg.GitRepoPullCompleted = {'requestUniqueId': str}


class Session(object):
    def __init__(self, server, testManager, machine_management, socket, address):
        self.server = server
        self.socket = socket
        self.address = address
        self.testManager = testManager
        self.machine_management = machine_management
        self.currentTestId = None
        self.currentDeploymentId = None
        self.socketLock = threading.Lock()
        self.machineId = None
        self.lastMessageTimestamp = time.time()

        logging.info("Incoming Server Connection initialized.")

    def stillLooksAlive(self):
        """Close socket if no traffic in a long time. Returns whether to keep polling..."""
        try:
            if time.time() - self.lastMessageTimestamp > 360:
                logging.info("Clearing out socket for machine %s as we have not heard from it in 360 seconds.", self.machineId)

                self.socket.shutdown(socket.SHUT_RDWR)
                self.socket.close()
                return False
            return True
        except:
            logging.error("Exception clearing old socket: %s", traceback.format_exc())
            return False

    def __call__(self):
        try:
            self.send(ServerToClientMsg.IdentifyCurrentState())

            while not self.server.shouldStop():
                msg = algebraic_to_json.Encoder().from_json(
                    json.loads(self.readString()),
                    ClientToServerMsg
                    )
                self.lastMessageTimestamp = time.time()
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
        if msg.matches.CurrentState:
            self.machineId = msg.machineId
            logging.info("WorkerChannel initialized with machineId=%s", self.machineId)
            
            self.testManager.machineInitialized(msg.machineId, time.time())

            if msg.state.matches.WorkingOnDeployment:
                deploymentId = msg.state.deploymentId

                if not self.testManager.handleDeploymentConnectionReinitialized(deploymentId, time.time(), msg.state.logs_so_far):
                    self.send(ServerToClientMsg.ShutdownDeployment(deploymentId))
                else:
                    self.currentDeploymentId = msg.state.deploymentId

                    def onMessage(msg):
                        if self.currentDeploymentId == deploymentId:
                            self.send(ServerToClientMsg.TerminalInput(deploymentId=deploymentId,msg=msg))
                    
                    self.testManager.subscribeToClientMessages(deploymentId, onMessage)

            elif msg.state.matches.WorkingOnTest:
                if not self.testManager.handleTestConnectionReinitialized(msg.state.testId, time.time(), msg.state.logs_so_far, msg.state.artifacts):
                    self.send(ServerToClientMsg.CancelTest(msg.state.testId))
                else:
                    self.currentTestId = msg.state.testId
            elif msg.state.matches.TestFinished:
                self.testManager.recordTestResults(msg.state.success, msg.state.testId, msg.state.testSuccesses, msg.state.artifacts, time.time())
                self.send(ServerToClientMsg.AcknowledgeFinishedTest(msg.state.testId))
        elif msg.matches.RequestPermissionToHitGitRepo:
            if self.currentDeploymentId != msg.curTestOrDeployId and self.currentTestId != msg.curTestOrDeployId:
                allowed = False
                logging.warn("Denying git repo hit for unknown test/deploy id %s", msg.curTestOrDeployId)
            else:
                try:
                    allowed = self.testManager.tryToAllocateGitRepoLock(msg.requestUniqueId, self.currentDeploymentId or self.currentTestId)
                except:
                    logging.error("Allocating git repo lock failed!\n:%s", traceback.format_exc())
                    allowed = False

            self.send(ServerToClientMsg.GrantOrDenyPermissionToHitGitRepo(requestUniqueId=msg.requestUniqueId, allowed=allowed))

        elif msg.matches.GitRepoPullCompleted:
            self.testManager.gitRepoLockReleased(msg.requestUniqueId)
        elif msg.matches.WaitingHeartbeat:
            if self.machineId is None:
                return

            self.testManager.machineHeartbeat(self.machineId, time.time())

            if self.currentDeploymentId is None and self.currentTestId is None:
                deploymentId, testDefinition = self.testManager.startNewDeployment(self.machineId, time.time())
                if deploymentId is not None:
                    self.currentDeploymentId = deploymentId
                    self.send(
                        ServerToClientMsg.DeploymentAssignment(
                            deploymentId=deploymentId,
                            testDefinition=testDefinition
                            )
                        )
                    def onMessage(msg):
                        if self.currentDeploymentId == deploymentId:
                            self.send(ServerToClientMsg.TerminalInput(deploymentId=deploymentId,msg=msg))
                    self.testManager.subscribeToClientMessages(deploymentId, onMessage)
                else:
                    t0 = time.time()
                    testId, testDefinition = self.testManager.startNewTest(self.machineId, time.time())
                    if testId is not None:
                        self.currentTestId = testId
                        self.send(
                            ServerToClientMsg.TestAssignment(
                                testId=testId,
                                testDefinition=testDefinition
                                )
                            )
                        logging.info("Allocated new test %s to machine %s in %s seconds.", testId, self.machineId, time.time() - t0)
        elif msg.matches.ArtifactUploaded:
            if msg.testId == self.currentTestId:
                self.testManager.recordTestArtifactUploaded(self.currentTestId, msg.artifact, time.time(), isCumulative=False)
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
        elif msg.matches.DeploymentExited:
            if msg.deploymentId == self.currentDeploymentId:
                self.testManager.shutdownDeployment(msg.deploymentId, time.time())
                self.send(ServerToClientMsg.ShutdownDeployment(msg.deploymentId))
                self.currentDeploymentId = None
        elif msg.matches.DeploymentHeartbeat or msg.matches.DeploymentTerminalOutput:
            log = msg.data if msg.matches.DeploymentTerminalOutput else None
            if msg.deploymentId == self.currentDeploymentId:
                if not self.testManager.handleMessageFromDeployment(msg.deploymentId, time.time(), log):
                    self.send(ServerToClientMsg.ShutdownDeployment(msg.deploymentId))
                    self.currentDeploymentId = None
        elif msg.matches.TestFinished:
            self.testManager.recordTestResults(msg.success, msg.testId, msg.testSuccesses, msg.artifacts, time.time())
            self.currentTestId = None
            self.send(ServerToClientMsg.AcknowledgeFinishedTest(msg.testId))

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
        self.sessions = []

    def executeManagerWork(self):
        try:
            lastSweep = None

            while not self.shouldStop():
                task = self.testManager.performBackgroundWork(time.time())

                if lastSweep is None or time.time() - lastSweep > CLEANUP_TASK_FREQUENCY:
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
        finally:
            logging.info("Manager worker thread exited")

    def port(self):
        return self.port_

    def initialize(self):
        logging.info("Initializing TestManager.")
        self.testManager.markRepoListDirty(time.time())

        #start something to touch all the objects we can reach in the
        #background
        touchAllThread = threading.Thread(
            target=self.testManager.touchAllTestsAndRuns,
            args=(time.time(),)
            )
        touchAllThread.daemon=True
        touchAllThread.start()

        try:
            self.testManager.pruneDeadWorkers(time.time())
        except:
            logging.error("Server had an exception during initialization:\n%s", traceback.format_exc())

        try:
            self.testManager.checkAllTestPriorities(time.time())
        except:
            logging.error("Server had an exception during initialization:\n%s", traceback.format_exc())
        
        logging.info("DONE Initializing TestManager.")
        

    def runListenLoop(self):
        logging.info("Starting TestLooperServer listen loop")

        self.httpServer.start()

        logging.info("HTTP server started")

        try:
            self.initialize()
            logging.info("TestLooper initialized")

            self.workerThread.start()

            super(TestLooperServer, self).runListenLoop()
        finally:
            self.httpServer.stop()
            logging.info("Listen loop stopped")

    def stop(self):
        super(TestLooperServer, self).stop()
        
        logging.info("waiting for worker thread...")

        self.workerThread.join()

        logging.info("successfully stopped TestLooperServer")

    def _onConnect(self, socket, address):
        logging.debug("Accepting connection from %s", address)
        newSession = Session(
            self,
            self.testManager,
            self.machine_management,
            socket,
            address
            )
        
        self.sessions.append(newSession)

        self.sessions = [
            x for x in self.sessions if x.stillLooksAlive()
            ]

        logging.info("Creating new session with %s sessions alive", len(self.sessions))

        threading.Thread(target=newSession).start()
