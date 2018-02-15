import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.rendering.ComboContexts as ComboContexts
import test_looper.server.rendering.BranchContext as BranchContext
import test_looper.server.rendering.IndividualTestGridRenderer as IndividualTestGridRenderer
import cgi

octicon = HtmlGeneration.octicon

class BranchAndConfigurationContext(BranchContext.BranchContext):
    def __init__(self, renderer, branchAndGroup, options):
        BranchContext.BranchContext.__init__(self, renderer, branchAndGroup.branch, options)
        self.configurationName = branchAndGroup.configurationName

    def renderLink(self):
        return HtmlGeneration.link(
            octicon("server") + self.configurationName, 
            self.urlString(), 
            "See individual test results for test configuration %s" % self.configurationName
            )

    def primaryObject(self):
        return ComboContexts.BranchAndConfiguration(self.branch, self.configurationName)

    def urlBase(self):
        return "repos/" + self.reponame + "/-/branches/" + self.branchname + "/-/configurations/" + self.configurationName

    def renderBreadcrumbPrefixes(self):
        return ["Configurations"]

    def getGridRenderer(self, commits):
        def testFun(c):
            for t in self.testManager.database.Test.lookupAll(commitData=c.data):
                if self.configurationName == self.testManager.configurationForTest(t):
                    yield t

        return IndividualTestGridRenderer.IndividualTestGridRenderer(commits, self, testFun)

    def childContexts(self, currentChild):
        return []

    def parentContext(self):
        return self.contextFor(self.branch)

    def renderNavbarLink(self):
        return self.renderLink()

    def renderMenuItemText(self, isHeader):
        return (octicon("server") if isHeader else "") + self.configurationName

    def contextViews(self):
        return []

    def renderMenuItemTitle(self, isHeader):
        return "Configuration " + self.configurationName
