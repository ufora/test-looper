
class ComboContext:
    def __cmp__(self, other):
        if not isinstance(other, type(self)):
            return cmp(type(other), type(self))
        return cmp(self.toTuple(), other.toTuple())

    def __hash__(self):
        return hash(self.toTuple())

class BranchAndFilter(ComboContext):
    def __init__(self, branch, configurationName, projectName):
        self.branch = branch
        self.configurationName = configurationName
        self.projectName = projectName

    def toTuple(self):
        return (self.branch, self.configurationName, self.projectName)

class CommitAndFilter(ComboContext):
    def __init__(self, commit, configurationName, projectName):
        self.commit = commit
        
        assert isinstance(configurationName, str) or configurationName is None
        assert isinstance(projectName, str) or projectName is None

        self.configurationName = configurationName
        self.projectName = projectName

    def toTuple(self):
        return (self.commit, self.configurationName, self.projectName)

    def shouldIncludeTest(self, test):
        if self.projectName and test.testDefinitionSummary.project != self.projectName:
            return False
        if self.configurationName and test.testDefinitionSummary.configuration != self.configurationName:
            return False
        return True


class IndividualTest(ComboContext):
    def __init__(self, context, individualTestName):
        """Represents an individually named test in the context of a Commit, Test, or TestRun"""
        self.context = context
        self.individualTestName = individualTestName

    def toTuple(self):
        return (self.context, self.individualTestName)

