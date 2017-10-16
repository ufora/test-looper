import time
import logging

class TestResultOnMachine(object):
    def __init__(self, success, testId, commitId, logMessages, performanceResults, machine, finishTime):
        """Create a TestResultOnMachine.

        successs - a bool
        logMessages - a list of log messages in case of failure
        performanceResults - a list of values from the PerformanceTestReporter. These should be
            picklable.
        machine - string identifying the machine that produced this result
        finishTime - a time.time() for when we finished the test
        """
        self.machine = machine
        self.testId = testId
        self.commitId = commitId
        self.success = success
        self.logMessages = logMessages
        self.performanceResults = performanceResults
        self.finishTime = finishTime

    def toJson(self):
        return {
            'machine': self.machine,
            'commitId': self.commitId,
            'testId': self.testId,
            'success': self.success,
            'logMessages': self.logMessages,
            'performanceResults': [x.toJson() for x in self.performanceResults],
            'finishTime': self.finishTime
            }

    @staticmethod
    def fromJson(json):
        return TestResultOnMachine(
            json['success'],
            json['testId'],
            json['commitId'],
            json['logMessages'],
            [PerformanceTestResult.fromJson(x) for x in json['performanceResults']],
            json['machine'],
            json['finishTime']
            )

    def recordPerformanceTests(self, perfTests):
        self.performanceResults += [PerformanceTestResult(x) for x in perfTests]

    def recordLogMessage(self, message):
        self.logMessages.append(({"time": time.time(), 'message': message},))

    def __str__(self):
        return repr(self)

    def __repr__(self):
        return "TestResultOnMachine(machine=%s,test=%s,commit=%s,success=%s,logCount=%s,perfcount=%s)" % (
            self.machine,
            str(self.testId)[:10],
            str(self.commitId)[:10],
            self.success,
            len(self.logMessages),
            len(self.performanceResults)
            )


class TestResult(object):
    RUNNING = 'running'
    TIMEOUT = 'timeout'
    PASSED = 'passed'
    FAILED = 'failed'

    def __init__(self,
            testName,
            testId,
            commitId,
            machine,
            machineToInternalIpMap,
            started,
            heartbeat,
            machineResults
            ):
        self.testName = testName
        self.testId = testId
        self.commitId = commitId
        self.machine = machine
        self.machineToInternalIpMap = machineToInternalIpMap
        self.started = started
        self.heartbeat = heartbeat
        self.machineResults = machineResults

    HEARTBEAT_TIMEOUT = 300.0
    HEARTBEAT_INTERVAL = 10.0

    HEARTBEAT_RESPONSE_ACK = 'ack'
    HEARTBEAT_RESPONSE_DONE = 'done'
    HEARTBEAT_RESPONSE_FAIL = 'fail'

    def toJson(self):
        return {
            'testName': self.testName,
            'testId': self.testId,
            'commitId': self.commitId,
            'machine': self.machine,
            'machineToInternalIpMap': self.machineToInternalIpMap,
            'started': self.started,
            'heartbeat': self.heartbeat,
            'machineResults': { k: v.toJson() for k,v in self.machineResults.iteritems() }
            }

    @staticmethod
    def fromJson(json):
        return TestResult(
            json['testName'],
            json['testId'],
            json['commitId'],
            json['machine'],
            json['machineToInternalIpMap'],
            json['started'],
            json['heartbeat'],
            { k: TestResultOnMachine.fromJson(v) for k,v in json['machineResults'].iteritems() }
            )

    @staticmethod
    def create(testName, testId, commitId, machineId, machineToInternalIpMap):
        return TestResult(
            testName,
            testId,
            commitId,
            machineId,
            machineToInternalIpMap,
            time.time(),
            {},
            {}
            )

    def createIpListToPassToScript(self):
        """Create the IP address list to pass to all the machines involved in the test."""
        headIp = self.machineToInternalIpMap[self.machine]

        for machine in sorted(self.machineToInternalIpMap.keys()):
            if machine != self.machine:
                headIp += " " + self.machineToInternalIpMap[machine]

        return headIp

    def __str__(self):
        return "TestResult(testId=%s, commit=%s, testName=%s, machineCount=%s)" % (
            self.testId[:10],
            self.commitId[:10],
            self.testName,
            len(self.machineToInternalIpMap)
            )

    def __repr__(self):
        return str(self)

    def heartbeatFromMachine(self, machine):
        self.heartbeat[machine] = time.time()

        leader = self.leaderMachine()
        if self.testName != 'build' and machine != leader and self.hasResultForMachine(leader):
            # The leader already reported the test completion status.
            leaderResult = self.machineResults[leader]
            logging.info("Telling machine %s to stop running its test because the leader " +
                         "already completed with result: %s",
                         machine, leaderResult)
            return TestResult.HEARTBEAT_RESPONSE_DONE if leaderResult.success \
                    else TestResult.HEARTBEAT_RESPONSE_FAIL

        if self.isTimeout():
            logging.info("Machine %s timed out running test '%s'. Responding to heartbeat with %s",
                         machine, self.testName, TestResult.HEARTBEAT_RESPONSE_FAIL)
            return TestResult.HEARTBEAT_RESPONSE_FAIL
        return TestResult.HEARTBEAT_RESPONSE_ACK

    def leaderMachine(self):
        return sorted(self.machineToInternalIpMap.itervalues())[0]

    def hasResultForMachine(self, machine):
        return machine in self.machineResults

    def getPerformanceTestResults(self):
        result = []
        for machineResult in self.machineResults.values():
            result += machineResult.performanceResults
        return result

    def recordMachineResult(self, machineResult):
        logging.info("Recording test %s result: %s", self.testName, machineResult)
        self.machineResults[machineResult.machine] = machineResult

    def masterMachineResultSuccessful(self):
        masterIp, masterResult = sorted([(ip, self.machineResults[name]) \
                                        for name, ip in self.machineToInternalIpMap.iteritems()])[0]
        return masterResult.success

    def status(self):
        if len(self.machineResults) == len(self.machineToInternalIpMap):
            return TestResult.PASSED if self.masterMachineResultSuccessful() else TestResult.FAILED

        if self.timeSinceHeartbeat() > TestResult.HEARTBEAT_TIMEOUT:
            return TestResult.TIMEOUT

        return TestResult.RUNNING

    def oldestHeartbeat(self):
        if not self.heartbeat:
            return self.startTime()
        return min(self.heartbeat.values())

    def timeSinceHeartbeat(self):
        oldest = self.oldestHeartbeat()

        return time.time() - oldest

    def finishedTime(self):
        if len(self.machineResults) == len(self.machineToInternalIpMap):
            return max(x.finishTime for x in self.machineResults.values())
        return None

    def isTimeout(self):
        return self.status() == TestResult.TIMEOUT

    def isRunning(self):
        return self.status() == TestResult.RUNNING

    def passed(self):
        status = self.status()
        return self.status() == TestResult.PASSED

    def failed(self):
        return self.status() == TestResult.FAILED

    def startTime(self):
        return self.started

    def minutesElapsed(self):
        performanceTestResults = self.getPerformanceTestResults()
        if len(performanceTestResults) == 0:
            elapsed = [self.secondsElapsed()]
        else:
            elapsed = [x.timeElapsed for x in performanceTestResults if x.timeElapsed is not None]
        if len(elapsed) == 0:
            return 0
        return max(elapsed) / 60

    def secondsElapsed(self):
        startTime = self.started

        if self.isTimeout():
            return self.oldestHeartbeat() - startTime

        finishTime = self.finishedTime()

        if finishTime is not None:
            return finishTime - startTime

        return time.time() - startTime

class PerformanceTestResult(object):
    """Model around the result of a single result made by the PerformanceTestReporter module."""
    def __init__(self, resultDict):
        self.resultDict = resultDict

    def toJson(self):
        return self.resultDict

    @staticmethod
    def fromJson(resultDict):
        return PerformanceTestResult(resultDict)

    @property
    def name(self):
        return self.resultDict['name']

    @property
    def timeElapsed(self):
        return self.resultDict['time']

    @property
    def metadata(self):
        return self.resultDict['metadata']

    def wasSuccessful(self):
        return self.timeElapsed is not None



