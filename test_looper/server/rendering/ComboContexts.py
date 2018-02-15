
class ComboContext:
    def __cmp__(self, other):
        if not isinstance(other, type(self)):
            return cmp(type(other), type(self))
        return cmp(self.toTuple(), other.toTuple())

    def __hash__(self):
        return hash(self.toTuple())

class BranchAndConfiguration(ComboContext):
    def __init__(self, branch, configurationName):
        self.branch = branch
        self.configurationName = configurationName

    def toTuple(self):
        return (self.branch, self.configurationName)

class CommitAndConfiguration(ComboContext):
    def __init__(self, commit, configurationName):
        self.commit = commit
        self.configurationName = configurationName

    def toTuple(self):
        return (self.commit, self.configurationName)

class IndividualTest(ComboContext):
    def __init__(self, test, individualTestName):
        self.test = test
        self.individualTestName = individualTestName

    def toTuple(self):
        return (self.test, self.individualTestName)

