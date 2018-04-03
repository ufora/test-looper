import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.rendering.ComboContexts as ComboContexts
import test_looper.server.rendering.CommitContext as CommitContext
import test_looper.server.rendering.IndividualTestGridRenderer as IndividualTestGridRenderer
import uuid
import cgi

octicon = HtmlGeneration.octicon
card = HtmlGeneration.card

class CommitAndConfigurationContext(CommitContext.CommitContext):
    def __init__(self, renderer, commitAndGroup, options):
        CommitContext.CommitContext.__init__(self, renderer, commitAndGroup.commit, options)
        self.configurationName = commitAndGroup.configurationName
        self.commit = commitAndGroup.commit

    def renderNavbarLink(self):
        return self.renderLink()

    def renderLink(self, nameIsNameInBranch=False):
        return HtmlGeneration.link(
                octicon("git-commit") + "HEAD" + self.nameInBranch
            if nameIsNameInBranch else 
                octicon("server") + self.configurationName, 
            self.urlString(), "See individual test results for test configuration %s" % self.configurationName
            )
    
    def allTests(self):
        return [x for x in 
            self.testManager.allTestsForCommit(self.commit)
                if self.testManager.configurationForTest(x) == self.configurationName]

    
    def primaryObject(self):
        return ComboContexts.CommitAndConfiguration(self.commit, self.configurationName)

    def urlBase(self):
        return "repos/" + self.commit.repo.name + "/-/commits/" + self.commit.hash + "/configurations/" + self.configurationName

    def tests(self):
        res = []
        for t in self.testManager.allTestsForCommit(self.commit):
            if self.configurationName == self.testManager.configurationForTest(t):
                res.append(t)

        return sorted(res, key=lambda t: (0 if t.testDefinitionSummary.type == "Build" else 1, t.name))

    def renderBreadcrumbPrefixes(self):
        return []

    def renderTestResultsGridByGroup(self):
        def testFun(commit):
            for t in self.testManager.allTestsForCommit(commit):
                if self.configurationName == self.testManager.configurationForTest(t) and t.testDefinitionSummary.type == "Test":
                    yield t

        rows = (self.commit,) + self.commit.data.parents

        renderer = IndividualTestGridRenderer.IndividualTestGridRenderer(
            rows,
            self, 
            testFun,
            lambda testGroup, row:
                self.contextFor(
                    ComboContexts.CommitAndConfiguration(row, self.configurationName)
                    ).withOptions(testGroup=testGroup).urlString(),
            displayIndividualTestsGraphically=False,
            breakOutIndividualTests=self.options.get("testGroup","") != ""
            )

        grid = [[""] + renderer.headers()]

        for commit in rows:
            link = self.contextFor(
                ComboContexts.CommitAndConfiguration(commit, self.configurationName)
                ).withOptions(**self.options).renderLink(nameIsNameInBranch=True)

            grid.append([link] + renderer.gridRow(commit))


        return HtmlGeneration.transposeGrid(grid)

    def childContexts(self, currentChild):
        if isinstance(currentChild.primaryObject(), self.database.Test):
            if currentChild.primaryObject().testDefinitionSummary.type == 'Build':
                return [self.contextFor(t)
                        for t in sorted(
                            self.testManager.allTestsForCommit(self.commit),
                            key=lambda t:t.testDefinitionSummary.name
                            ) if t.testDefinitionSummary.type == "Build"
                        and self.testManager.configurationForTest(t) == self.configurationName
                        ]
            if currentChild.primaryObject().testDefinitionSummary.type == 'Test':
                return [self.contextFor(t)
                        for t in sorted(
                            self.testManager.allTestsForCommit(self.commit),
                            key=lambda t:t.testDefinitionSummary.name
                            ) if t.testDefinitionSummary.type == "Test"
                        and self.testManager.configurationForTest(t) == self.configurationName
                        ]
        
        return []

    def parentContext(self):
        return self.contextFor(self.commit)

    def renderMenuItemText(self, isHeader):
        return (octicon("server") if isHeader else "") + self.configurationName

    def renderMenuItemTitle(self, isHeader):
        return "Configuration " + self.configurationName

    def contextViews(self):
        return ["test_results", "test_builds", "test_suites"]
    
