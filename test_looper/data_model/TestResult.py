import time
import logging

HEARTBEAT_RESPONSE_ACK = 'ack'

class TestResultOnMachine(object):
    def __init__(self, success, testId, repoName, commitHash, logMessages, performanceResults, machine, finishTime):
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
        self.repoName = repoName
        self.commitHash = commitHash
        self.success = success
        self.logMessages = logMessages
        self.performanceResults = performanceResults
        self.finishTime = finishTime

    def toJson(self):
        return {
            'machine': self.machine,
            'repoName': self.repoName,
            'commitHash': self.commitHash,
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
            json['repoName'],
            json['commitHash'],
            json['logMessages'],
            [],
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
            str(self.repoName) +
                str(self.commitHash)[:10],
            self.success,
            len(self.logMessages),
            len(self.performanceResults)
            )

