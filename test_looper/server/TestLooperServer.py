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
        is_new_machine = self.testManager.recordMachineObservation(args.machineId)
        heartbeatResponse = self.testManager.heartbeat(args.testId,
                                                       args.commitId,
                                                       args.machineId)

        self.writeString(heartbeatResponse)

        if is_new_machine:
            self.cloud_connection.tagInstance(args.machineId)


    def getTask(self, machineInfo):
        commit = None

        if machineInfo.machineId is not None:
            is_new_machine = self.testManager.recordMachineObservation(machineInfo.machineId)

        try:
            t0 = time.time()
            commit, testDefinition, testResult = \
                self.testManager.getTask(machineInfo)

            if commit:
                testName = testDefinition.testName
                logging.info("Took %.2f sec to select test %s for worker %s.",
                             time.time() - t0,
                             testName,
                             self.address)
                testDefinition = commit.getTestDefinitionFor(testName)

                self.writeString(
                    json.dumps({
                        "commitId": commit.commitId,
                        'testId': testResult.testId,
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
        with self.testManager.lock:
            is_new_machine = self.testManager.recordMachineObservation(result.machine)
            self.testManager.recordMachineResult(result)

        if is_new_machine and self.cloud_connection:
            self.cloud_connection.tagInstance(result.machine)

        if not result.success and self.cloud_connection:
            logging.info("Test result from client at %s: %s, machine: %s",
                         self.address,
                         result,
                         result.machine)
            isAlive = self.cloud_connection.isMachineAlive(result.machine)

            if not isAlive:
                testId = result.testId
                commitId = result.commitId
                self.testManager.clearResultsForTestIdCommitId(testId, commitId)
                logging.info("%s, %s returned an invalid test result, purged from db",
                             testId,
                             result.machine)


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

    def port(self):
        return self.port_

    def initialize(self):
        with self.testManager.lock:
            self.testManager.initialize()

    def runListenLoop(self):
        logging.info("Starting TestLooperServer listen loop")

        self.httpServer.start()

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
        logging.info("successfully stopped TestLooperServer")

    def _onConnect(self, socket, address):
        logging.debug("Accepting connection from %s", address)
        threading.Thread(target=Session(self.testManager,
                                        self.cloud_connection,
                                        socket,
                                        address)).start()
