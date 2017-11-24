import logging
import math
import time

import test_looper.data_model.TestStats as TestStats
import test_looper.data_model.TestResult as TestResult

class Commit(object):
    """Models a single Commit in the test database."""
    def __init__(self, 
            testDb,
            commitId, 
            parentHashes, 
            subject, 
            testScriptDefinitions, 
            testScriptDefinitionsError
            ):
        self.testDb = testDb

        #commitID is a combination of a reponame and a commitish
        assert len(commitId.split("/")) == 2

        for h in parentHashes:
            assert "/" not in h

        self.commitId = commitId
        self.repoName, self.commitHash = commitId.split("/")
        self.parentHashes = parentHashes
        self.subject = subject
        self.branches = set()
        self.testScriptDefinitions = testScriptDefinitions
        self.testScriptDefinitionsError = testScriptDefinitionsError
        self.testsById = {}
        self.testIdsByType = {}
        self.statsByType = {}
        self.isTargetedTestCache = {}

        for definition in self.testScriptDefinitions:
            self.statsByType[definition.testName] = TestStats.TestStats()

    @property
    def parentHash(self):
        if not self.parentHashes:
            return None
        return self.parentHashes[-1]

    @property
    def parentId(self):
        if self.parentHash is None:
            return None
        return self.repoName + "/" + self.parentHash

    @property
    def parentIds(self):
        return [self.repoName + "/" + h for h in self.parentHashes]

    def getTestDefinitionFor(self, testName):
        for testDef in self.testScriptDefinitions:
            if testDef.testName == testName:
                return testDef

        return None

    def isPeriodicTest(self, testName):
        definition = self.getTestDefinitionFor(testName)
        return definition.periodicTest

    def clearTestResult(self, testName, testId):
        test_result = self.testsById[testId]

        #this test no longer exists
        del self.testsById[testId]

        #remove it from the lookup table
        self.testIdsByType[testName].remove(testId)

        #reset the stats for this particular test
        #and rebuild them
        self.statsByType[testName] = TestStats.TestStats()
        for testId in self.testIdsByType[testName]:
            self.statsByType[testName].addTest(self.testsById[testId])

    def testChanged(self, testName, result):
        if testName in self.statsByType:
            self.statsByType[testName].dirtyCache()

    def dirtyTestPriorityCache(self):
        self.isTargetedTestCache = {}

    def isTargetedTest(self, testName):
        if testName in self.isTargetedTestCache:
            return self.isTargetedTestCache[testName]
        else:
            result = self.computeIsTargetCommitAndTestName(testName)
            self.isTargetedTestCache[testName] = result
            return result

    def computeIsTargetCommitAndTestName(self, testName):
        for b in self.branches:
            if testName in b.targetedTestList() and (
                    self.commitId in b.targetedCommitIds() or not b.targetedCommitIds()
                    ):
                return True
            if self.commitId in b.targetedCommitIds() and not b.targetedTestList():
                return True

        return False

    @property
    def isUnderTest(self):
        return any(b.isUnderTest for b in self.branches)

    def __repr__(self):
        return "Commit(repo=%s, commitId='%s', parentHash='%s', subject='%s')" % \
                (self.commitId.split("/")[0], self.commitId.split("/")[1], self.parentHash, self.subject)

    def __str__(self):
        return self.__repr__()

    def heartbeatTest(self, testId, machineId):
        if testId in self.testsById:
            return self.testsById[testId].heartbeatFromMachine(machineId)
        else:
            logging.warn("TestId %s doesn't exist, so we can't heartbeat it", testId)
            return TestResult.TestResult.HEARTBEAT_RESPONSE_DONE

    def lastTestRunStarted(self, testName):
        return self.statsByType[testName].lastTestRunStarted()

    def addTestResult(self, result, updateDB):
        hasPreviousResult = result.testId in self.testsById

        self.testsById[result.testId] = result

        if not hasPreviousResult:
            # this is the first time we're seeing this testId.
            # add it to the testType->testId index
            testGroup = self.testIdsByType.get(result.testName)
            if not testGroup:
                testGroup = self.testIdsByType[result.testName] = []
            testGroup.append(result.testId)

            #also add it to the database
            if updateDB:
                self.testDb.updateTestListForCommit(self)

        if result.testName not in self.statsByType:
            self.statsByType[result.testName] = TestStats.TestStats()

        self.statsByType[result.testName].addTest(result)

        for branch in self.branches:
            branch.dirtySequentialFailuresCache()

    @staticmethod
    def mean_and_stddev(values):
        if not values:
            return None, None
        mean = float(sum(values))/len(values)
        stddev = (sum((v - mean)**2 for v in values)/len(values)) ** 0.5
        return mean, stddev


    def testStatByType(self, testName):
        if testName in self.statsByType:
            return self.statsByType[testName]
        else:
            return TestStats.TestStats()

    def testStatByTypeGroup(self, testNamePrefix):
        res = TestStats.TestStats()
        for testName in self.statsByType:
            if testName.startswith(testNamePrefix):
                testStat = self.statsByType[testName]
                res = res.combinedWith(testStat)
        return res

    def needsBuild(self):
        if not 'build' in self.statsByType:
            return True
        if (self.statsByType['build'].runningCount > 0 or
                self.statsByType['build'].passCount > 0 or
                self.statsByType['build'].failCount > 0):
            return False
        return True

    def nextTestToRun(self):
        if not 'build' in self.statsByType:
            return 'build'

        candidates = sorted([(s.completedCount + s.runningCount, name) \
                                for name, s in self.statsByType.iteritems() if name != 'build'])
        if len(candidates) == 0:
            return None
        # return the name of the least tested category
        return candidates[0][1]

    def clearTestResults(self):
        self.testsById = {}
        self.testIdsByType = {}

        #make sure we keep the list of test types around. they are implicitly stored in the
        #keys of 'statsByType'
        oldStatsByType = self.statsByType
        self.statsByType = {}
        for statType in oldStatsByType:
            self.statsByType[statType] = TestStats.TestStats()

    def isBrokenBuild(self):
        buildStats = self.statsByType.get('build')
        return buildStats and \
               buildStats.completedCount >= Commit.MAX_BUILD_ATTEMPTS and \
               buildStats.failCount == 1

    def buildInProgress(self):
        return 'build' in self.statsByType and self.statsByType['build'].runningCount > 0

    def fullPassesCompleted(self):
        """
        Determine how many passes of *all* tests have been completed on this commit.
        """
        if not self.hasTestStats:
            return 0

        return min(
            [s.completedCount for name, s in self.statsByType.iteritems() if name != 'build']
            )

    def totalCompletedTestRuns(self):
        ''' The total number of tests run in all categories.'''
        if not self.hasTestStats:
            return 0
        return sum([s.completedCount for name, s in self.statsByType.iteritems() if name != 'build'])

    @property
    def hasTestStats(self):
        return len(self.statsByType) > 0 and self.statsByType.keys() != ['build']

    def excludeFromTestingBecauseOfCommitSubject(self):
        return "[nft]" in self.subject

    def totalRunningCount(self):
        res = 0

        for s in self.statsByType:
            res += self.runningCount(s)

        return res

    def runningCount(self, testType):
        stats = self.statsByType.get(testType)
        if not stats:
            return 0
        return stats.runningCount

    def timeoutCount(self, testType):
        stats = self.statsByType.get(testType)
        if not stats:
            return 0
        return stats.timeoutCount

    def totalNonTimedOutRuns(self, testType):
        stats = self.statsByType.get(testType)
        if not stats:
            return 0
        return stats.runningCount + stats.passCount + stats.failCount

    def passRate(self):
        passRateByTestType = \
                [self.passRateForTestGroup(group) for group in self.statsByType.iterkeys()]
        return None if any(pr is None for pr in passRateByTestType) else \
            reduce(lambda x, y: x*y, passRateByTestType, 1.0)

    def passRateForTestGroup(self, groupName):
        stats = self.statsByType[groupName]
        return stats.passCount / float(stats.completedCount) if stats.completedCount != 0 else None

    def totalElapsed(self):
        totalMinutesByType = [s.totalMinutes for s in self.statsByType.itervalues()]
        return sum(totalMinutesByType) if totalMinutesByType else 0.0
