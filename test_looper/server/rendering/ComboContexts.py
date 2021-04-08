
class ComboContext:
    def __eq__(self, other):
        if not isinstance(other, type(self)):
            return False
        return self.toTuple() == other.toTuple()

    def __hash__(self):
        return hash(self.toTuple())

class BranchAndFilter(ComboContext):
    def __init__(self, branch, configurationName, projectName, parentLevel=0):
        self.branch = branch
        self.configurationName = configurationName
        self.projectName = projectName
        self.parentLevel = parentLevel

    def toTuple(self):
        return (self.branch, self.configurationName, self.projectName, self.parentLevel)

class CommitAndFilter(ComboContext):
    def __init__(self, commit, configurationName, projectName, parentLevel=0):
        #parentLevel is a way of making commit and filter contexts whose children are at
        #different levels:
        #   0 means both config and project are set
        #   1 means the config is not specified, but the project is.
        #   2 means neither config nor project is specified and children are projects.
        #this is just so we can keep track of the breadcrumbs as we go up the tree.

        self.commit = commit
        self.parentLevel = parentLevel
        
        assert isinstance(configurationName, str) or configurationName is None
        assert isinstance(projectName, str) or projectName is None

        self.configurationName = configurationName
        self.projectName = projectName

    def toTuple(self):
        return (self.commit, self.configurationName, self.projectName, self.parentLevel)

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

