import test_looper.core.TestResult as TestResult
from test_looper.core.TestScriptDefinition import TestScriptDefinition

class TestDatabase(object):
    def __init__(self, kvStore):
        self.kvStore = kvStore
        self.dbPrefix = "1_"

    def getTestIdsForCommit(self, commitId):
        tests = self.kvStore.get(self.dbPrefix + "commit_tests_" + commitId)

        if tests:
            return tests
        return []

    def loadTestResultForTestId(self, testId):
        res = self.kvStore.get(self.dbPrefix + "test_" + testId)
        if not res:
            return res

        return TestResult.TestResult.fromJson(res)

    def clearResultsForTestIdCommitId(self, testId, commitId):
        self.kvStore.delete(self.dbPrefix + "test_" + testId)
        testIds = self.kvStore.get(self.dbPrefix + "commit_tests_" + commitId)
        if testIds is None:
            return
        filtered = [testId for testId in testIds if testId != testId]
        self.kvStore.set(self.dbPrefix + "commit_tests_" + commitId, filtered)


    def clearAllTestsForCommitId(self, commitId):
        ids = self.getTestIdsForCommit(commitId)

        for testId in ids:
            self.kvStore.delete(self.dbPrefix + "test_" + testId)

        self.kvStore.delete(self.dbPrefix + "commit_tests_" + commitId)

    def updateTestListForCommit(self, commit):
        ids = sorted(commit.testsById.keys())

        self.kvStore.set(self.dbPrefix + "commit_tests_" + commit.commitId, ids)

    def updateTestResult(self, result):
        self.kvStore.set(self.dbPrefix + "test_" + result.testId, result.toJson())

    def getTestScriptDefinitionsForCommit(self, commitId):
        res = self.kvStore.get("commit_test_definitions_" + commitId)
        if res is None:
            return None

        return [TestScriptDefinition.fromJson(x) for x in res]

    def setTestScriptDefinitionsForCommit(self, commit, result):
        self.kvStore.set("commit_test_definitions_" + commit, [x.toJson() for x in result])

    def getTargetedTestTypesForBranch(self, branchname):
        return self.kvStore.get("branch_targeted_tests_" + branchname) or []

    def setTargetedTestTypesForBranch(self, branchname, testNames):
        return self.kvStore.set("branch_targeted_tests_" + branchname, testNames)

    def getTargetedCommitIdsForBranch(self, branchname):
        return self.kvStore.get("branch_targeted_commit_ids_" + branchname) or []

    def setTargetedCommitIdsForBranch(self, branchname, commitIds):
        return self.kvStore.set("branch_targeted_commit_ids_" + branchname, commitIds)

    def getBranchIsUnderTest(self, branchname):
        result = self.kvStore.get("branch_is_deep_test_" + branchname)
        if result is None:
            if branchname == "origin/master":
                return True
            else:
                return False
        return result

    def setBranchIsUnderTest(self, branchname, isUnderTest):
        return self.kvStore.set("branch_is_deep_test_" + branchname, isUnderTest)
