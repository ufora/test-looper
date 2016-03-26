import time

class TestStats(object):
    """TestStats - models statistics about a set of TestResult objects."""
    UPDATE_INTERVAL = 30.0

    def __init__(self):
        self.testDict = {}
        self.totalElapsedMinutes_ = 0
        self.runningElapsedMinutes_ = 0
        self.runningCount_ = 0
        self.passCount_ = 0
        self.failCount_ = 0
        self.completedCount_ = 0
        self.timeoutCount_ = 0

        self.lastCacheUpdateTime = None
        self.lastTestResult = None

    def __repr__(self):
        return "TestStats(%s)" % self.__dict__

    def combinedWith(self, other):
        res = TestStats()
        res.updateCache()

        res.runningCount_ = self.runningCount_ + other.runningCount_
        res.timeoutCount_ = self.timeoutCount_ + other.timeoutCount_
        res.totalElapsedMinutes_ = self.totalElapsedMinutes_ + other.totalElapsedMinutes_
        res.runningElapsedMinutes_ = self.runningElapsedMinutes_ + other.runningElapsedMinutes_

        if not self.completedCount_:
            res.passCount_ = other.passCount_
            res.failCount_ = other.failCount_
        elif not other.completedCount_:
            res.passCount_ = self.passCount_
            res.failCount_ = self.failCount_
        else:
            avgPassRate = self.passrate() * other.passrate()
            totalCt = min(self.completedCount_, other.completedCount_)

            res.passCount_ = avgPassRate * totalCt
            res.failCount_ = totalCt - res.passCount_

        res.completedCount_ = res.passCount_ + res.failCount_

        return res

    def passrate(self):
        return float(self.passCount_) / float(self.completedCount_)

    def dirtyCache(self):
        self.lastCacheUpdateTime = None

    def cacheNeedsUpdate(self):
        if self.lastCacheUpdateTime is None:
            return True

        if self.runningCount_ > 0 and time.time() - self.lastCacheUpdateTime > TestStats.UPDATE_INTERVAL:
            return True

        return False

    def updateCache(self):
        if self.cacheNeedsUpdate():
            self.totalElapsedMinutes_ = self.calcTotalElapsedMinutes()
            self.runningElapsedMinutes_ = self.calcRunningElapsedMinutes()
            self.runningCount_ = self.calcRunningCount()
            self.passCount_ = self.calcPassCount()
            self.failCount_ = self.calcFailCount()
            self.timeoutCount_ = self.calcTimeoutCount()
            self.completedCount_ = self.calcCompletedCount()

            self.lastCacheUpdateTime = time.time()

    def lastTestRunStarted(self):
        if self.lastTestResult is None:
            return None
        return self.lastTestResult.started

    @property
    def tests(self):
        return self.testDict.values()

    @property
    def totalElapsedMinutes(self):
        self.updateCache()
        return self.totalElapsedMinutes_

    @property
    def runningElapsedMinutes(self):
        self.updateCache()
        return self.runningElapsedMinutes_

    @property
    def totalMinutes(self):
        self.updateCache()
        return self.totalMinutes_

    @property
    def runningCount(self):
        self.updateCache()
        return self.runningCount_

    @property
    def passCount(self):
        self.updateCache()
        return self.passCount_

    @property
    def failCount(self):
        self.updateCache()
        return self.failCount_

    @property
    def timeoutCount(self):
        self.updateCache()
        return self.timeoutCount_

    @property
    def completedCount(self):
        self.updateCache()
        return self.completedCount_

    def addTest(self, testResult):
        self.dirtyCache()
        self.lastTestResult = testResult
        self.testDict[testResult.testId] = testResult

    def calcTotalElapsedMinutes(self):
        return sum([test.minutesElapsed() if test.passed() or test.failed() else 0.0 for test in self.tests])

    def calcRunningElapsedMinutes(self):
        return sum([test.minutesElapsed() if test.isRunning() else 0.0 for test in self.tests])

    def calcRunningCount(self):
        return sum([1 if test.isRunning() else 0 for test in self.tests])

    def calcPassCount(self):
        return sum([1 if test.passed() else 0 for test in self.tests])

    def calcFailCount(self):
        return sum([1 if test.failed() else 0 for test in self.tests])

    def calcTimeoutCount(self):
        return sum([1 if test.isTimeout() else 0 for test in self.tests])

    def calcCompletedCount(self):
        return sum([1 if test.passed() or test.failed() else 0 for test in self.tests])


