import json
import socket
import time
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
        self._socketLock = threading.Lock()
        self._socket = self._connect()
        self._msgQueue = Queue.Queue()
        self._readThread = threading.Thread(target=self._readLoop)
        self._curTestId = None
        self._curDeploymentId = None
        self._subscriptions = {}
        self._subscriptionsLock = threading.Lock()

        self._readThread.start()

    def stop(self):
        logging.info("TestLooperClient stopping")
        try:
            if not self._readThread:
                return

            self._socket.shutdown(socket.SHUT_RDWR)    
            self._socket.close()

            self._readThread.join()
            self._readThread = None
        finally:
            logging.info("TestLooperClient stopped")

    def _connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if self.use_ssl:
            s = ssl.wrap_socket(s)

        try:
            s.connect((self.host, self.port))
        except:
            logging.info("Failed to connect to %s:%s", self.host, self.port)
            raise

        return s

    def _readLoop(self):
        while True:
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
                        s.append(msg.bytes)
                    else:
                        try:
                            s(msg.bytes)
                        except:
                            logging.error("Failed to callback subscription to terminal input: %s", traceback.format_exc())
            else:
                self._msgQueue.put(msg)

    def _readString(self):
        return socket_util.readString(self._socket)

    def _writeString(self, s):
        with self._socketLock:
            return socket_util.writeString(self._socket, s)

    def send(self, msg):
        self._writeString(json.dumps(algebraic_to_json.Encoder().to_json(msg)))

    def checkoutWork(self, waitTime):
        t0 = time.time()
        while time.time() - t0 < waitTime:
            self.send(TestLooperServer.ClientToServerMsg.WaitingHeartbeat(machineId=self.machineId))

            msg = None
            try:
                msg = self._msgQueue.get(timeout=min(TestLooperClient.HEARTBEAT_INTERVAL, waitTime - (time.time() - t0)))
            except Queue.Empty as e:
                pass

            if msg is not None:
                if msg.matches.TestAssignment:
                    self._curTestId = msg.testId
                    return msg.repoName, msg.commitHash, msg.testName, msg.testId, False

                if msg.matches.DeploymentAssignment:
                    self._curDeploymentId = msg.deploymentId
                    return msg.repoName, msg.commitHash, msg.testName, msg.deploymentId, True

    def consumeMessages(self):
        try:
            while True:
                m = self._msgQueue.get_nowait()
                if m.matches.CancelTest and self._curTestId == m.testId:
                    self._curTestId = None
                if m.matches.ShutdownDeployment and self._curDeploymentId == m.deploymentId:
                    self._curDeploymentId = None
        except Queue.Empty:
            pass

    def heartbeat(self, msg=None):
        self.consumeMessages()

        if self._curTestId is not None:
            if msg is None:
                self.send(TestLooperServer.ClientToServerMsg.TestHeartbeat(testId=self._curTestId))
            else:
                self.send(TestLooperServer.ClientToServerMsg.TestLogOutput(testId=self._curTestId, log=msg))

        elif self._curDeploymentId is not None:
            if msg is None:
                self.send(TestLooperServer.ClientToServerMsg.DeploymentHeartbeat(deploymentId=self._curDeploymentId))
            else:
                self.send(TestLooperServer.ClientToServerMsg.DeploymentTerminalOutput(deploymentId=self._curDeploymentId, data=msg.replace("\n","\r\n")))

        else:
            raise Exception("No active test or deployment")

    def terminalOutput(self, output):
        if self._curDeploymentId is not None:
            self.send(TestLooperServer.ClientToServerMsg.DeploymentTerminalOutput(deploymentId=self._curDeploymentId, data=output))

    def subscribeToTerminalInput(self, callback):
        if self._curDeploymentId is None:
            return

        with self._subscriptionsLock:
            existing = self._subscriptions.get(self._curDeploymentId)
            self._subscriptions[self._curDeploymentId] = callback

            for e in existing:
                try:
                    callback(e)
                except:
                    logging.error("Error passing terminal input to callback: %s", traceback.format_exc())

    def publishTestResult(self, succeeded, individualTestSuccesses):
        assert self._curTestId is not None

        self.send(TestLooperServer.ClientToServerMsg.TestFinished(testId=self._curTestId, success=succeeded, testSuccesses=individualTestSuccesses))
        self._curTestId = None