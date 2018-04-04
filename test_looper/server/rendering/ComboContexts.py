
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
        self.configurationName = configurationName
        self.projectName = projectName

    def toTuple(self):
        return (self.commit, self.configurationName, self.projectName)

class IndividualTest(ComboContext):
    def __init__(self, test, individualTestName):
        self.test = test
        self.individualTestName = individualTestName

    def toTuple(self):
        return (self.test, self.individualTestName)

