class CommitAndTestToRun(object):
    def __init__(self, testName, commit, priority):
        self.testName = testName
        self.commit = commit
        self.priority = priority

    def testDefinition(self):
        return self.commit.getTestDefinitionFor(self.testName)
