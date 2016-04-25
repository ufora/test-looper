import logging
import time
import test_looper.server.SequentialFailureRates as SequentialFailureRates

class Branch(object):
    """Models a set of commits (usually from a git branch)"""
    def __init__(self, testDb, branchName, commitRevlist):
        self.testDb = testDb
        self.branchName = branchName
        self.commits = {}
        self.commitsInOrder = []
        self.commitIdToIndex = {}

        self.commitRevlist = commitRevlist
        self.isDeepTestCache = None
        self.sequentialFailuresCache10 = None
        self.sequentialFailuresCache100 = None
        self.sequentialFailuresCache1000 = None
        self.lastPeriodicTestRun = None

    def __repr__(self):
        return "Branch(branchName='%s')" % self.branchName

    def __str__(self):
        return self.__repr__()

    def dirtySequentialFailuresCache(self):
        self.sequentialFailuresCache10 = None
        self.sequentialFailuresCache100 = None
        self.sequentialFailuresCache1000 = None

    def failureRateAndIndexForCommitIdAndTest(self, commitId, testType):
        self.updateSequentialFailuresCache()

        if testType not in self.sequentialFailuresCache1000:
            return None

        if commitId not in self.commitIdToIndex:
            return None

        return (
            self.sequentialFailuresCache1000[testType],
            len(self.commits) - self.commitIdToIndex[commitId] - 1
            )


    def commitIsStatisticallyNoticeableFailureRateBreak(self, commitId, testType):
        self.updateSequentialFailuresCache()

        if testType in self.sequentialFailuresCache10 and commitId in self.commitIdToIndex:
            indexInSFR = len(self.commits) - self.commitIdToIndex[commitId] - 1

            for level, cache in [(.001, self.sequentialFailuresCache1000),
                                 (.01, self.sequentialFailuresCache100),
                                 (.1, self.sequentialFailuresCache10)]:
                break_direction = cache[testType].isBreak(indexInSFR)
                if break_direction:
                    return level, break_direction

        return None, None

    def updateSequentialFailuresCache(self):
        if self.sequentialFailuresCache10 is not None:
            return

        self.sequentialFailuresCache10 = {}
        self.sequentialFailuresCache100 = {}
        self.sequentialFailuresCache1000 = {}

        testTypes = set()

        for commit in self.commits.values():
            for testType in commit.statsByType:
                testTypes.add(testType)

        for t in testTypes:
            self.sequentialFailuresCache10[t] = SequentialFailureRates.SequentialFailureRates(0.1)
            self.sequentialFailuresCache100[t] = SequentialFailureRates.SequentialFailureRates(0.01)
            self.sequentialFailuresCache1000[t] = SequentialFailureRates.SequentialFailureRates(0.001)

        for c in reversed(self.commitsInOrder):
            for testName in testTypes:
                stat = c.testStatByType(testName)
                self.sequentialFailuresCache10[testName].add(stat.failCount, stat.completedCount)
                self.sequentialFailuresCache100[testName].add(stat.failCount, stat.completedCount)
                self.sequentialFailuresCache1000[testName].add(stat.failCount, stat.completedCount)


    def updateRevList(self, revList, testManager):
        if revList != self.commitRevlist:
            self.commitRevlist = revList
            self.updateCommitsUnderTest(testManager)

    @staticmethod
    def orderCommits(commits):
        #crappy n^2 way to order commits

        commitList = []
        commitIdsUsed = set()

        allCommitIds = {}
        for c in commits:
            allCommitIds[c.commitId] = c

        while len(commitList) < len(commits):
            foundOne = False

            for c in commits:
                if commitList and c.commitId == commitList[-1].parentId and c.commitId not in commitIdsUsed:
                    foundOne = True
                    commitList.append(c)
                    commitIdsUsed.add(c.commitId)

                    break

            if not foundOne:
                #pick a new leaf. A 'leaf' is a commit who is not yet used and which is not the
                #parent of any commit remaining to be used
                possibleLeafIds = set(allCommitIds.keys()) - commitIdsUsed
                leaves = set(possibleLeafIds)

                for c in commits:
                    if c.commitId in possibleLeafIds:
                        leaves.discard(c.parentId)

                if leaves:
                    commitId = sorted(list(leaves))[0]
                    c = allCommitIds[commitId]

                    commitList.append(c)
                    commitIdsUsed.add(c.commitId)

                    foundOne = True

            assert foundOne, "Failed to increase the commit list."

        return commitList

    @property
    def isDeepTest(self):
        if self.isDeepTestCache is not None:
            return self.isDeepTestCache

        self.isDeepTestCache = self.testDb.getBranchIsDeepTestBranch(self.branchName)
        return self.isDeepTestCache

    def setIsDeepTest(self, isDeepTest):
        self.isDeepTestCache = isDeepTest
        return self.testDb.setBranchIsDeepTestBranch(self.branchName, isDeepTest)

    def targetedTestList(self):
        return self.testDb.getTargetedTestTypesForBranch(self.branchName)

    def setTargetedTestList(self, testNames):
        for commit in self.commits.values():
            commit.dirtyTestPriorityCache()
        return self.testDb.setTargetedTestTypesForBranch(self.branchName, testNames)

    def targetedCommitIds(self):
        return list(
            set(self.testDb.getTargetedCommitIdsForBranch(self.branchName)).intersection(
                set(self.commits.keys())
                )
            )

    def setTargetedCommitIds(self, commitIds):
        for commit in self.commits.values():
            commit.dirtyTestPriorityCache()
        return self.testDb.setTargetedCommitIdsForBranch(self.branchName, commitIds)

    def updateCommitsUnderTest(self, testManager, lock=None):
        if lock:
            lock.release()
        t0 = time.time()
        commitIdsParentsAndTitles = testManager.github.commitsInRevList(self.commitRevlist)

        if lock:
            lock.acquire()

        t0 = time.time()
        commitIds = set([c[0] for c in commitIdsParentsAndTitles])

        commitsToDiscard = set(self.commits.keys()) - commitIds
        newCommitIds = commitIds - set(self.commits.keys())

        for c in commitsToDiscard:
            self.commits[c].branches.discard(self)
            self.commits[c].dirtyTestPriorityCache()
            del self.commits[c]

        for commitId, parentHashes, commitTitle in commitIdsParentsAndTitles:
            if commitId in newCommitIds:
                self.commits[commitId] = testManager.createCommit(commitId,
                                                                  parentHashes,
                                                                  commitTitle)
                self.commits[commitId].branches.add(self)
                self.commits[commitId].dirtyTestPriorityCache()

        self.commitsInOrder = Branch.orderCommits(self.commits.values())

        for index, commit in enumerate(self.commitsInOrder):
            self.commitIdToIndex[commit.commitId] = index
        diff = time.time() - t0
        if diff > .5:
            logging.info("updating commits in memory took %s seconds", diff)

    def getPerfDataSummary(self):
        series = {}

        for c in self.commits.values():
            for test in c.testsById.values():
                for perfStat in test.getPerformanceTestResults():
                    if perfStat.name not in series:
                        series[perfStat.name] = {}

                    commitDict = series[perfStat.name]

                    if test.commitId not in commitDict:
                        commitDict[test.commitId] = []

                    commitDict[test.commitId].append(perfStat.timeElapsed)

        return series

