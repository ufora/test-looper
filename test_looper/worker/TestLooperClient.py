import json
import socket
import time
import uuid
import logging
import traceback
import ssl
import threading
import test_looper.core.socket_util as socket_util
import test_looper.server.TestLooperServer as TestLooperServer
import test_looper.core.algebraic_to_json as algebraic_to_json
import base64
import Queue

class ProtocolMismatchException(Exception):
    pass

class TestLooperClient(object):
    HEARTBEAT_INTERVAL = 10.0

    def __init__(self, host, port, use_ssl, machineId):
        self.host = host
        self.port = port
        self.machineId = machineId
        self.use_ssl = use_ssl
        self._shouldStop = False
        self._socketLock = threading.Lock()
        self._socket = None
        self._clientToServerMessageQueue = Queue.Queue()
        self._serverToClientMessageQueue = Queue.Queue()
        self._hitRepoPermissionQueues = {}
        self._readThread = threading.Thread(target=self._readLoop)
        self._writeThread = threading.Thread(target=self._writeLoop)
        self._curTestId = None
        self._curTestResults = None
        self._curDeploymentId = None
        self._curOutputs = None
        self._curArtifacts = None
        self._subscriptions = {}
        self._subscriptionsLock = threading.Lock()

        self._writeThread.start()
        self._readThread.start()

    def stop(self):
        logging.info("TestLooperClient for %s stopping", self.machineId)
        self._shouldStop = True

        try:
            if not self._readThread:
                return

            self._socket.shutdown(socket.SHUT_RDWR)    
            self._socket.close()

            self._serverToClientMessageQueue.put(None)
            self._clientToServerMessageQueue.put(None)

            self._readThread.join()
            self._readThread = None

            self._writeThread.join()
            self._writeThread = None
        finally:
            logging.info("TestLooperClient for %s stopped", self.machineId)

    def _connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if self.use_ssl:
            s = ssl.wrap_socket(s)

        try:
            s.connect((self.host, self.port))
            logging.info("Connected to %s:%s", self.host, self.port)
        except:
            logging.info("Failed to connect to %s:%s for %s", self.host, self.port, self.machineId)
            raise

        return s

    def _writeLoop(self):
        while not self._shouldStop and self._socket is None:
            time.sleep(0.01)
        logging.info("TestLooperClient for %s connected...", self.machineId)

        while not self._shouldStop:
            msg = self._clientToServerMessageQueue.get()
            if msg is not None:
                try:
                    self._writeString(json.dumps(algebraic_to_json.Encoder().to_json(msg)))
                except:
                    logging.error("Failed to send message %s to server:\n\n%s", msg, traceback.format_exc())

    def _readLoop(self):
        while not self._shouldStop:
            msg = algebraic_to_json.Encoder().from_json(
                json.loads(self._readString()),
                TestLooperServer.ServerToClientMsg
                )
            if msg.matches.TerminalInput:
                with self._subscriptionsLock:
                    if msg.deploymentId not in self._subscriptions:
                        self._subscriptions[msg.deploymentId] = []
                    s = self._subscriptions[msg.deploymentId]

                    if s is None:
                        pass
                    if isinstance(s,list):
                        s.append(msg.msg)
                    else:
                        try:
                            s(msg.msg)
                        except:
                            logging.error("Failed to callback subscription to terminal input: %s", traceback.format_exc())
            else:
                self._serverToClientMessageQueue.put(msg)

    def _readString(self):
        """Read a single string from the other socket.

        If we get disconnected, try to reconnect!
        """
        while not self._shouldStop:
            try:
                while self._socket is None and not self._shouldStop:
                    try:
                        self._socket = self._connect()
                    except:
                        logging.error("Socket connect failed. Retrying.\n\n%s", traceback.format_exc())
                        time.sleep(5.0)

                return socket_util.readString(self._socket)
            except:
                logging.error("Socket write failed: trying to reconnect.\n\n%s", traceback.format_exc())
                self._socket = None

    def _writeString(self, s):
        return socket_util.writeString(self._socket, s)

    def _send(self, msg):
        self._clientToServerMessageQueue.put(msg)

    def checkoutWork(self, waitTime):
        t0 = time.time()
        while time.time() - t0 < waitTime:
            self._send(TestLooperServer.ClientToServerMsg.WaitingHeartbeat())

            msg = None
            try:
                msg = self._serverToClientMessageQueue.get(timeout=min(TestLooperClient.HEARTBEAT_INTERVAL, waitTime - (time.time() - t0)))
            except Queue.Empty as e:
                pass

            if msg is not None:
                if msg.matches.IdentifyCurrentState:
                    self._send(
                        TestLooperServer.ClientToServerMsg.CurrentState(
                            machineId=self.machineId,
                            state=TestLooperServer.WorkerState.Waiting()
                            )
                        )
                if msg.matches.TestAssignment:
                    self._curTestId = msg.testId
                    self._curOutputs = []
                    self._curArtifacts = []
                    logging.info("New TestID is %s", self._curTestId)
                    return msg.testId, msg.testDefinition, False

                if msg.matches.DeploymentAssignment:
                    self._curDeploymentId = msg.deploymentId
                    self._curOutputs = []
                    self._curArtifacts = []
                    logging.info("New deploymentId is %s", self._curDeploymentId)
                    return msg.deploymentId, msg.testDefinition, True

    def consumeMessages(self):
        try:
            while not self._shouldStop:
                m = self._serverToClientMessageQueue.get_nowait()
                if m is None:
                    return
                if m.matches.IdentifyCurrentState:
                    if self._curTestId and self._curTestResults is not None:
                        workerState=TestLooperServer.WorkerState.TestFinished(
                            testId=self._curTestId,
                            success=self._curTestResults['success'],
                            artifacts=self._curArtifacts,
                            testSuccesses=self._curTestResults['testSuccesses']
                            )
                    elif self._curTestId is not None:
                        workerState=TestLooperServer.WorkerState.WorkingOnTest(
                            testId=self._curTestId,
                            logs_so_far="".join(self._curOutputs),
                            artifacts=self._curArtifacts
                            )
                    elif self._curDeploymentId is not None:
                        workerState=TestLooperServer.WorkerState.WorkingOnDeployment(
                            deploymentId=self._curDeploymentId,
                            logs_so_far="".join(self._curOutputs)
                            )
                    else:
                        workerState=TestLooperServer.WorkerState.Waiting()

                    self._send(
                        TestLooperServer.ClientToServerMsg.CurrentState(
                            machineId=self.machineId,
                            state=workerState
                            )
                        )
                if m.matches.GrantOrDenyPermissionToHitGitRepo:
                    self._hitRepoPermissionQueues[m.requestUniqueId].put(m.allowed)
                if m.matches.AcknowledgeFinishedTest and self._curTestId == m.testId:
                    logging.info("TestLooperServer acknowledged test completion.")
                    self._curTestId = None
                    self._curOutputs = None
                    self._curArtifacts = None
                    self._curTestResults = None
                if m.matches.CancelTest and self._curTestId == m.testId:
                    logging.info("TestLooper canceling test %s", self._curTestId)
                    self._curTestId = None
                    self._curOutputs = None
                    self._curArtifacts = None
                    self._curTestResults = None
                if m.matches.ShutdownDeployment and self._curDeploymentId == m.deploymentId:
                    logging.info("TestLooper canceling deployment %s", self._curDeploymentId)
                    self._curDeploymentId = None
                    self._curOutputs = None
                    self._curArtifacts = None
        except Queue.Empty:
            pass

    def heartbeat(self, msg=None):
        if self._shouldStop:
            raise Exception("Shutting down")

        self.consumeMessages()

        if self._curTestId is not None:
            if msg is None:
                self._send(TestLooperServer.ClientToServerMsg.TestHeartbeat(testId=self._curTestId))
            else:
                self._curOutputs.append(msg)
                self._send(TestLooperServer.ClientToServerMsg.TestLogOutput(testId=self._curTestId, log=msg))

        elif self._curDeploymentId is not None:
            if msg is None:
                self._send(TestLooperServer.ClientToServerMsg.DeploymentHeartbeat(deploymentId=self._curDeploymentId))
            else:
                self._curOutputs.append(msg)
                self._send(TestLooperServer.ClientToServerMsg.DeploymentTerminalOutput(deploymentId=self._curDeploymentId, data=msg.replace("\n","\r\n")))
        else:
            raise Exception("No active test or deployment")

    def requestPermissionToHitGitRepo(self):
        """Request permission to hit the git repo and then block. Returns a guid if successful.
        Otherwise None. Throws if we get disconnected.
        """

        reqId = str(uuid.uuid4())

        curId = self._curTestId or self._curDeploymentId
        queue = Queue.Queue()

        self._hitRepoPermissionQueues[reqId] = queue

        self._send(TestLooperServer.ClientToServerMsg.RequestPermissionToHitGitRepo(requestUniqueId=reqId, curTestOrDeployId=curId))

        #now poll until we have permission
        t0 = time.time()
        while True:
            try:
                if time.time() - t0 > 10:
                    #something's wrong - try again
                    self._send(TestLooperServer.ClientToServerMsg.RequestPermissionToHitGitRepo(requestUniqueId=reqId, curTestOrDeployId=curId))

                if queue.get(timeout=.1):
                    return reqId
                else:
                    return None

            except Queue.Empty:
                if curId != (self._curTestId or self._curDeploymentId):
                    raise Exception("Server canceled the test.")

    def releaseGitRepoLock(self, requestUniqueId):
        self._send(TestLooperServer.ClientToServerMsg.GitRepoPullCompleted(requestUniqueId))

    def recordArtifactUploaded(self, artifact):
        if self._shouldStop:
            raise Exception("Shutting down")

        self.consumeMessages()

        if self._curTestId is not None:
            self._curArtifacts.append(artifact)
            self._send(TestLooperServer.ClientToServerMsg.ArtifactUploaded(testId=self._curTestId, artifact=artifact))
        else:
            raise Exception("No active test")

    def scopedReadLockAroundGitRepo(self):
        class Scope:
            def __init__(scope):
                scope.reqId = None

            def __enter__(scope, *args):
                while scope.reqId is None:
                    scope.reqId = self.requestPermissionToHitGitRepo()
                    if not scope.reqId:
                        time.sleep(5.0)

            def __exit__(scope, *args, **kwargs):
                if scope.reqId:
                    self.releaseGitRepoLock(scope.reqId)

        return Scope()


    def terminalOutput(self, output):
        if self._curDeploymentId is not None:
            self._curOutputs.append(output)
            self._send(TestLooperServer.ClientToServerMsg.DeploymentTerminalOutput(deploymentId=self._curDeploymentId, data=output))

    def subscribeToTerminalInput(self, callback):
        if self._curDeploymentId is None:
            return

        with self._subscriptionsLock:
            existing = self._subscriptions.get(self._curDeploymentId)
            self._subscriptions[self._curDeploymentId] = callback

            if existing:
                for e in existing:
                    try:
                        callback(e)
                    except:
                        logging.error("Error passing terminal input to callback: %s", traceback.format_exc())

    def deploymentExitedEarly(self):
        assert self._curDeploymentId is not None

        self._send(TestLooperServer.ClientToServerMsg.DeploymentExited(deploymentId=self._curDeploymentId))

    def publishTestResult(self, succeeded, individualTestSuccesses):
        assert self._curTestId is not None

        self._curTestResults = {'success': succeeded, 'testSuccesses': individualTestSuccesses}
        self._send(TestLooperServer.ClientToServerMsg.TestFinished(testId=self._curTestId, success=succeeded, artifacts=self._curArtifacts, testSuccesses=individualTestSuccesses))

        while self._curTestId is not None:
            self.consumeMessages()
            time.sleep(.1)
