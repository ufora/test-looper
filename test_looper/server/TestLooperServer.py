import collections
import json
import logging
import requests
import threading
import traceback
import time

import test_looper.core.SimpleServer as SimpleServer
import test_looper.core.socket_util as socket_util
import test_looper.core.TestResult as TestResult

class LockWithTimer(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.initialLockTime = None

    def acquire(self):
        self.lock.acquire()
        self.initialLockTime = time.time()

    def release(self):
        if time.time() - self.initialLockTime > 2.0:
            logging.warn("Manager lock held for %s seconds.\n%s",
                         time.time() - self.initialLockTime,
                         "".join(traceback.format_stack()))
        self.lock.release()

    def __enter__(self):
        self.lock.__enter__()
        self.initialLockTime = time.time()

    def __exit__(self, *args):
        if time.time() - self.initialLockTime > 2.0:
            logging.warn("Manager lock held for %s seconds.\n%s",
                         time.time() - self.initialLockTime,
                         "".join(traceback.format_stack()))
        self.lock.__exit__(*args)


WorkerInfo = collections.namedtuple('WorkerInfo',
                                    'machineId internalIp coreCount instanceType')

HeartbeatArguments = collections.namedtuple('HeartbeatArguments',
                                            'commitId testId machineId')

class Session(object):
    def __init__(self, testManager, ec2Connection, socket, address):
        self.socket = socket
        self.address = address
        self.testManager = testManager
        self.ec2Connection = ec2Connection

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
                self.getTask(WorkerInfo(**args))
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
        with self.testManager.lock:
            is_new_machine = self.testManager.recordMachineObservation(args.machineId)
            heartbeatResponse = self.testManager.heartbeat(args.testId,
                                                           args.commitId,
                                                           args.machineId)

            self.writeString(heartbeatResponse)

        if is_new_machine:
            self.ec2Connection.tagInstance(args.machineId)

    def getTask(self, workerInfo):
        commit = None

        with self.testManager.lock:
            if workerInfo.machineId is not None:
                is_new_machine = self.testManager.recordMachineObservation(workerInfo.machineId)

            try:
                t0 = time.time()
                commit, testDefinition, testResult = \
                    self.testManager.getTask(workerInfo)

                if commit:
                    testName = testDefinition.testName
                    logging.info("Test to run is %s. Took %s to find it.",
                                 testName,
                                 time.time() - t0)
                    logging.debug("Test assignment for client at %s: %s",
                                  self.address,
                                  testResult)

                    testDefinition = commit.getTestDefinitionFor(testName)

                    self.writeString(
                        json.dumps({
                            "test": testResult.toJson(),
                            'testScriptDefinition': testDefinition.toJson()
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

        if is_new_machine:
            self.ec2Connection.tagInstance(workerInfo.machineId)

    def publishTestResult(self, testResultAsJson):
        result = TestResult.TestResultOnMachine.fromJson(testResultAsJson)
        with self.testManager.lock:
            is_new_machine = self.testManager.recordMachineObservation(result.machine)
            self.testManager.recordMachineResult(result)

        if is_new_machine:
            self.ec2Connection.tagInstance(result.machine)

        if not result.success and self.ec2Connection:
            logging.info("Test result from client at %s: %s, machine: %s",
                         self.address,
                         result,
                         result.machine)
            isAlive = self.ec2Connection.isMachineAlive(result.machine)

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
    protocolVersion = '2.2.3'

    def __init__(self, port, testManager, httpServer, ec2Connection):
        """
        Initialize a TestLooperServer
        """
        SimpleServer.SimpleServer.__init__(self, port)

        self.port_ = port
        self.testManager = testManager
        self.httpServer = httpServer
        self.refreshThread = threading.Thread(target=self._refreshLoop)
        self.ec2Connection = ec2Connection


    def port(self):
        return self.port_


    def initialize(self):
        with self.testManager.lock:
            self.testManager.initialize()


    def runListenLoop(self):
        logging.info("Starting TestLooperServer listen loop")

        self.httpServer.start()

        logging.info("HTTP server started")

        self.initialize()
        logging.info("TestLooper initialized")

        self.refreshThread.start()
        logging.info("Refresh thread started")

        super(TestLooperServer, self).runListenLoop()

        self.httpServer.stop()
        logging.info("Listen loop stopped")

    def stop(self):
        super(TestLooperServer, self).stop()

        try:
            self.refreshThread.join(5.0)
        except RuntimeError:
            logging.warn("Thread wasn't started...")

        if self.refreshThread.isAlive():
            logging.warn("Refresh thread did not join within the specified timeout")

        logging.info("successfully stopped TestLooperServer")

    def _onConnect(self, socket, address):
        logging.debug("Accepting connection from %s", address)
        threading.Thread(target=Session(self.testManager,
                                        self.ec2Connection,
                                        socket,
                                        address)).start()

    def _refreshLoop(self):
        # refresh the list of commits that need to be tested every minute
        while not self.getStopFlag().wait(60.0):
            # wait timed out - refresh self.testManager
            try:
                with self.testManager.lock:
                    self.testManager.refresh(self.testManager.lock)
            except:
                logging.error("Error in test manager refresh loop: %s", traceback.format_exc())
        logging.info("Exiting test manager refresh loop")


