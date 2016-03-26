import time
import threading

class TestLooperHttpServerEventLog(object):
    def __init__(self, kvStore):
        self.kvStore = kvStore
        self.lock = threading.Lock()
        self.logMessageCount = self.kvStore.get("http_server_log_count") or 0

    def getLogMessageByIndex(self, index):
        with self.lock:
            assert index >= 0 and index < self.logMessageCount, "Index %s out of range" % index
            assert isinstance(index, int)

            return self.kvStore.get("http_server_action_log_%s" % index)

    def getTopNLogMessages(self, count):
        with self.lock:
            bottomLogIndex = max(0, self.logMessageCount - count)
            topLogIndex = self.logMessageCount

        return self.getLogMessagesInRange(bottomLogIndex, topLogIndex)

    def getLogMessagesInRange(self, lowIndex, highIndex):
        return [self.getLogMessageByIndex(x) for x in range(lowIndex, highIndex)]

    def addLogMessage(self, currentLogin, message, *args):
        with self.lock:
            if args:
                message = message % args

            newLogMessage = {"date": time.ctime(), "message": message, "user": currentLogin}

            self.kvStore.set("http_server_action_log_%s" % self.logMessageCount, newLogMessage)
            
            self.logMessageCount += 1
            self.kvStore.set("http_server_log_count", self.logMessageCount)
