import logging
import time
import test_looper.server.SequentialFailureRates as SequentialFailureRates

class Branch(object):
    """Models a set of commits (usually from a git branch)"""
    def __init__(self, testDb, branchName, baselineBranchNameInRepo):
        self.testDb = testDb
        assert len(branchName.split("/")) > 1, "%s is not a valid repo/branchname" % branchName
        self.branchName = branchName
        self.baselineBranchNameInRepo = baselineBranchNameInRepo
        self.commits = {}
        self.commitsInOrder = []
        self.commitIdToIndex = {}

        self.isUnderTest_ = None
        self.sequentialFailuresCache10 = None
        self.sequentialFailuresCache100 = None
        self.sequentialFailuresCache1000 = None

    @property
    def repoName(self):
        return self.branchName.split("/")[0]

    def __repr__(self):
        return "Branch(%s)" % self.branchName

    def __str__(self):
        return self.__repr__()

    # Commit
    def dirtySequentialFailuresCache(self):
        self.sequentialFailuresCache10 = None
        self.sequentialFailuresCache100 = None
        self.sequentialFailuresCache1000 = None

    # Commit
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


    # HttpServer
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


    @staticmethod
    def orderCommits(commits):
        #crappy n^2 way to order commits

        commitList = []
        commitHashesUsed = set()

        allCommitIds = {}
        for c in commits:
            allCommitIds[c.commitHash] = c

        while len(commitList) < len(commits):
            foundOne = False

            for c in commits:
                if commitList and c.commitHash == commitList[-1].parentHash and c.commitHash not in commitHashesUsed:
                    foundOne = True
                    commitList.append(c)
                    commitHashesUsed.add(c.commitHash)

                    break

            if not foundOne:
                #pick a new leaf. A 'leaf' is a commit who is not yet used and which is not the
                #parent of any commit remaining to be used
                possibleLeafIds = set(allCommitIds.keys()) - commitHashesUsed
                leaves = set(possibleLeafIds)

                for c in commits:
                    if c.commitHash in possibleLeafIds:
                        leaves.discard(c.parentHash)

                if leaves:
                    commitHash = sorted(list(leaves))[0]
                    c = allCommitIds[commitHash]

                    commitList.append(c)
                    commitHashesUsed.add(c.commitHash)

                    foundOne = True

            assert foundOne, "Failed to increase the commit list."

        return commitList

    # HttpServer
    @property
    def isUnderTest(self):
        if self.isUnderTest_ is not None:
            return self.isUnderTest_

        self.isUnderTest_ = self.testDb.getBranchIsUnderTest(self.branchName)
        return self.isUnderTest_

    # HttpServer
    def setIsUnderTest(self, isUnderTest):
        self.isUnderTest_ = isUnderTest
        return self.testDb.setBranchIsUnderTest(self.branchName, isUnderTest)

    # Commit, HttpServer
    def targetedTestList(self):
        return self.testDb.getTargetedTestTypesForBranch(self.branchName)

    # HttpServer
    def setTargetedTestList(self, testNames):
        for commit in self.commits.values():
            commit.dirtyTestPriorityCache()
        return self.testDb.setTargetedTestTypesForBranch(self.branchName, testNames)

    # Commit, HttpServer
    def targetedCommitIds(self):
        return list(
            set(self.testDb.getTargetedCommitIdsForBranch(self.branchName)).intersection(
                set(self.commits.keys())
                )
            )

    # HttpServer
    def setTargetedCommitIds(self, commitIds):
        for commit in self.commits.values():
            commit.dirtyTestPriorityCache()
        return self.testDb.setTargetedCommitIdsForBranch(self.branchName, commitIds)

    # TestManager
    def updateCommitsUnderTest(self, testManager):
        t0 = time.time()

        repoName, branchNameInRepo = self.branchName.split("/")
        repo = testManager.source_control.getRepo(repoName)

        depth = testManager.settings.baseline_depth
        
        if branchNameInRepo == self.baselineBranchNameInRepo:
            commitHashesParentsAndTitles = \
                repo.commitsLookingBack(branchNameInRepo, depth)
        else:
            commitHashesParentsAndTitles = \
                repo.commitsBetweenBranches(
                    branchNameInRepo, 
                    self.baselineBranchNameInRepo
                    )

        if not commitHashesParentsAndTitles:
            commitHashesParentsAndTitles = \
                repo.commitsLookingBack(branchNameInRepo, depth)

        while len(commitHashesParentsAndTitles) < depth and commitHashesParentsAndTitles:
            #keep following the first parent...
            if len(commitHashesParentsAndTitles[-1]) > 1 and commitHashesParentsAndTitles[-1][1]:
                to_add = repo.source_repo.hashParentsAndCommitTitleFor(commitHashesParentsAndTitles[-1][1][0])
            else:
                to_add = None

            if to_add:
                commitHashesParentsAndTitles += [to_add]
            else:
                break

        t0 = time.time()
        commitHashes = set([c[0] for c in commitHashesParentsAndTitles])
        existingCommitHashes = set([c.commitHash for c in self.commits.values()])

        for c in list(self.commits.values()):
            if c.commitHash not in commitHashes:
                c.branches.discard(self)
                c.dirtyTestPriorityCache()
                del self.commits[c.commitId]

        for commitHash, parentHashes, commitTitle in commitHashesParentsAndTitles:
            if commitHash not in existingCommitHashes:
                commitId = repoName+"/"+commitHash

                self.commits[commitId] = testManager.createCommit(commitId,
                                                                  parentHashes,
                                                                  commitTitle
                                                                  )
                self.commits[commitId].branches.add(self)
                self.commits[commitId].dirtyTestPriorityCache()

        commitsByHash = {c.commitHash: c for c in self.commits.values()}

        self.commitsInOrder = [commitsByHash[hash] for hash, _, _ in commitHashesParentsAndTitles]

        self.commitIdToIndex = {
            commit.commitId: index
            for index, commit in enumerate(self.commitsInOrder)
            }

        diff = time.time() - t0
        if diff > .5:
            logging.info("updating %s commits in memory took %s seconds", len(commitsByHash), diff)

    # HttpServer
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
