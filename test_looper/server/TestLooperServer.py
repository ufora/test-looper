import collections
import json
import logging
import threading
import traceback
import time

import test_looper.core.SimpleServer as SimpleServer
import test_looper.core.socket_util as socket_util
import test_looper.data_model.TestResult as TestResult
import test_looper.core.cloud.MachineInfo as MachineInfo

HeartbeatArguments = collections.namedtuple('HeartbeatArguments',
                                            'commitId testId machineId')


class Session(object):
    def __init__(self, testManager, cloud_connection, socket, address):
        self.socket = socket
        self.address = address
        self.testManager = testManager
        self.cloud_connection = cloud_connection


    def __call__(self):
        try:
            if not self.handshake(TestLooperServer.protocolVersion):
                return

            self.processRequest()

        except socket_util.SocketException as e:
            logging.info("Socket error: %s", e.message)
        except:
            logging.error("Exception: %s", traceback.format_exc())
        finally:
            self.socket.close()


    def handshake(self, serverProtocolVersion):
        logging.debug("Waiting for client to initiate handshake")
        clientProtocolVersion = self.readString()

        if clientProtocolVersion != serverProtocolVersion:
            logging.error("protocol version mismatch: %s", clientProtocolVersion)
            self.writeString('error:protocol_version_mismatch')
            self.socket.close()
            return False

        self.writeString('protocol_match')
        logging.debug("Handshake completed successfully")
        return True


    def processRequest(self):
        requestJsonStr = self.readString()
        requestDict = json.loads(requestJsonStr)

        requestType = requestDict["request"]
        args = requestDict["args"]

        assert len(requestDict) == 2

        try:
            if requestType == 'getTask':
                self.getTask(MachineInfo.MachineInfo.fromJson(args))
            elif requestType == 'publishTestResult':
                self.publishTestResult(args)
            elif requestType == 'heartbeat':
                self.heartbeat(HeartbeatArguments(**args))
            else:
                self.writeString('error:protocol_violation:unknown_request')
                raise Exception("Protocol violation: unknown request type '%s'" % requestType)
        except:
            logging.error("%s", traceback.format_exc())
            self.writeString('error:protocol_violation:unknown_error')


    def heartbeat(self, args):
        self.testManager.testHeartbeat(args.testId, time.time())

        self.writeString("OK")

    def getTask(self, machineInfo):
        commit = None

        if machineInfo.machineId is not None:
            is_new_machine = self.testManager.recordMachineHeartbeat(machineInfo.machineId, time.time())

        try:
            t0 = time.time()
            commitId, testName, testId = self.testManager.startNewTest(machineInfo.machineId, time.time())

            if commitId:
                self.writeString(
                    json.dumps({
                        "commitId": commitId,
                        'testId': testId,
                        'testName': testName
                        })
                    )
            else:
                logging.debug("No tests assigned to client at %s", self.address)
                self.writeString(json.dumps(None))
        except:
            logging.error("Error getting the set of tests to run %s, %s",
                          self.address,
                          traceback.format_exc())
            self.writeString(json.dumps(None))

        if is_new_machine and self.cloud_connection:
            self.cloud_connection.tagInstance(machineInfo.machineId)


    def publishTestResult(self, testResultAsJson):
        result = TestResult.TestResultOnMachine.fromJson(testResultAsJson)

        self.testManager.recordTestResults(result.success, result.testId, time.time())

    def readString(self):
        return socket_util.readString(self.socket)

    def writeString(self, s):
        return socket_util.writeString(self.socket, s)

class TestLooperServer(SimpleServer.SimpleServer):
    #if we modify this protocol version, the loopers should reboot and pull a new copy of the code
    protocolVersion = '2.2.6'

    def __init__(self, port, testManager, httpServer, cloud_connection):
        """
        Initialize a TestLooperServer
        """
        SimpleServer.SimpleServer.__init__(self, port)

        self.port_ = port
        self.testManager = testManager
        self.httpServer = httpServer
        self.cloud_connection = cloud_connection
        self.workerThread = threading.Thread(target=self.executeManagerWork)

    def executeManagerWork(self):
        while not self.shouldStop():
            task = self.testManager.performBackgroundWork(time.time())
            if task is None:
                time.sleep(.1)

    def port(self):
        return self.port_

    def initialize(self):
        self.testManager.markRepoListDirty(time.time())

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
        self.workerThread.join()

        logging.info("successfully stopped TestLooperServer")

    def _onConnect(self, socket, address):
        logging.debug("Accepting connection from %s", address)
        threading.Thread(target=Session(self.testManager,
                                        self.cloud_connection,
                                        socket,
                                        address)).start()
