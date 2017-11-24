import json
import socket
import time
import logging
import traceback

import test_looper.core.socket_util as socket_util
import test_looper.data_model.TestResult as TestResult
import test_looper.server.TestLooperServer as TestLooperServer

class ProtocolMismatchException(Exception):
    pass

class TestLooperClient(object):
    HEARTBEAT_INTERVAL = 10.0

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.shouldStop = False

    def stop(self):
        self.shouldStop = True

    def connect_(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect((self.host, self.port))
        except:
            logging.info("Failed to connect to %s:%s", self.host, self.port)
            raise
        socket_util.writeString(s, TestLooperServer.TestLooperServer.protocolVersion)
        if socket_util.readString(s) == "protocol_match":
            return s
        else:
            raise ProtocolMismatchException()

    def getTask(self, machineInfo):
        def requestHandler(request_socket):
            socket_util.writeString(
                request_socket,
                json.dumps({
                    "request": "getTask",
                    "args": machineInfo.toJson()
                    })
                )
            response = socket_util.readString(request_socket)
            self.raiseIfError(response)
            return json.loads(response)

        return self.sendRequest(requestHandler)

    def publishTestResult(self, testResult, timeoutSeconds=600):
        '''
        testOutput: a TestResult.TestResultOnMachine instance
        '''
        assert isinstance(testResult, TestResult.TestResultOnMachine)

        def requestHandler(request_socket):
            socket_util.writeString(
                request_socket,
                json.dumps({"request": "publishTestResult", "args": testResult.toJson()})
                )

        logging.info("Publishing test result: %s.", testResult)

        t0 = time.time()
        while True:
            if self.shouldStop:
                raise ProtocolMismatchException()

            try:
                return self.sendRequest(requestHandler)
            except ProtocolMismatchException:
                raise
            except:
                if time.time() - t0 > timeoutSeconds:
                    raise
                else:
                    logging.warn("Couldn't publish test results: %s", traceback.format_exc())
                    time.sleep(1.0)

    def heartbeat(self, testId, commitId, machineId):
        def requestHandler(request_socket):
            socket_util.writeString(
                request_socket,
                json.dumps({
                    "request": "heartbeat",
                    "args": {
                        'testId':testId,
                        'commitId': commitId,
                        'machineId': machineId
                        }
                    })
                )

            result = socket_util.readString(request_socket)
            return result

        for ix in range(3):
            try:
                return self.sendRequest(requestHandler)
            except ProtocolMismatchException:
                raise
            except Exception as e:
                logging.warn("Tried to heartbeat for testId=%s, but failed: %s",
                             testId,
                             e)
                time.sleep(1.0)

        logging.warn("Skipping heartbeat")
        # Pretend that the server sent an ack. We don't want to abort any
        # running tests or stop heartbeating.
        return TestResult.TestResult.HEARTBEAT_RESPONSE_ACK


    def sendRequest(self, requestHandler):
        connected_socket = self.connect_()
        try:
            return requestHandler(connected_socket)
        finally:
            connected_socket.close()

    @staticmethod
    def raiseIfError(response):
        if response.startswith('error:'):
            raise Exception(response)


