import test_looper.server.rendering.Context as Context
import test_looper.server.rendering.ComboContexts as ComboContexts
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.core.ArtifactStorage as ArtifactStorage
import logging

card = HtmlGeneration.card

class TestRunContext(Context.Context):
    def __init__(self, renderer, testRun, options):
        Context.Context.__init__(self, renderer, options)
        self.testRun = testRun
        self.test = self.testRun.test
        self.commit = self.testManager.oldestCommitForTest(self.test)
        self.repo = self.commit.repo

    def consumePath(self, path):
        return None, path

    def primaryObject(self):
        return self.testRun

    def urlBase(self):
        prefix = "repos/" + self.repo.name + "/-/commits/"

        return prefix + self.commit.hash + "/tests/" + self.test.testDefinitionSummary.name + "/-/" + self.testRun._identity

    def renderNavbarLink(self):
        return self.renderLink(includeCommit=False, includeTest=False)

    def renderLink(self, includeCommit=True, includeTest=True):
        res = ""

        if includeCommit:
            res = self.contextFor(self.commit).renderLink()
        
        if includeTest:
            if res:
                res = res + "/"

            res = res + HtmlGeneration.link(self.test.testDefinitionSummary.name, self.contextFor(self.test).urlString())

        if res:
            res = res + "/"

        return res + HtmlGeneration.link(self.testRun._identity[:8], self.urlString())

    def renderBreadcrumbPrefixes(self):
        return ["Runs"]

    def contextViews(self):
        if self.test.testDefinitionSummary.type == "Build":
            return []
        else:
            return ["tests", "artifacts"]

    def renderViewMenuItem(self, view):
        if view == "artifacts":
            return "Artifacts"
        if view == "tests":
            return "Test Results"
        return view

    def renderViewMenuMouseoverText(self, view):
        if view == "artifacts":
            return "All test artifacts"
        if view == "tests":
            return "Individual test results"
        return ""

    def renderPageBody(self):
        if self.test.testDefinitionSummary.type == "Build":
            return self.artifactsForTestRunGrid()

        if self.currentView() == "artifacts":
            return self.artifactsForTestRunGrid()
        if self.currentView() == "tests":
            return self.individualTestReport()
        
    def individualTestReport(self):
        testRun = self.testRun

        if testRun.totalTestCount:
            individual_tests_grid = [["TEST_NAME", "PASSED"]]
            pass_dict = {}

            for ix in xrange(len(testRun.testNames.test_names)):
                pass_dict[testRun.testNames.test_names[ix]] = "PASS" if testRun.testFailures[ix] else "FAIL"

            for k,v in sorted(pass_dict.items()):
                individual_tests_grid.append((k,v))

            return HtmlGeneration.grid(individual_tests_grid)
        else:
            return card("No Individual Tests Reported")

    def artifactsForTestRunGrid(self):
        testRun = self.testRun

        grid = [["Artifact", "Size"]]

        if testRun.test.testDefinitionSummary.type == "Build":
            for artifact in testRun.test.testDefinitionSummary.artifacts:
                full_name = testRun.test.testDefinitionSummary.name + ("/" + artifact if artifact else "")

                build_key = self.renderer.artifactStorage.sanitizeName(full_name) + ".tar.gz"

                if self.renderer.artifactStorage.build_exists(testRun.test.hash, build_key):
                    grid.append([
                        HtmlGeneration.link(full_name + ".tar.gz", self.renderer.buildDownloadUrl(testRun.test.hash, build_key)),
                        HtmlGeneration.bytesToHumanSize(self.renderer.artifactStorage.build_size(testRun.test.hash, build_key))
                        ])

        for artifactName, sizeInBytes in self.renderer.artifactStorage.testResultKeysForWithSizes(testRun.test.hash, testRun._identity):
            name = self.renderer.artifactStorage.unsanitizeName(artifactName)
            
            if not name.startswith(ArtifactStorage.TEST_LOG_NAME_PREFIX):
                grid.append([
                    HtmlGeneration.link(
                        name,
                        self.renderer.testResultDownloadUrl(testRun._identity, artifactName)
                        ),
                    HtmlGeneration.bytesToHumanSize(sizeInBytes)
                    ])

        if not grid:
            return card("No Test Artifacts produced")

        return HtmlGeneration.grid(grid)

    def childContexts(self, currentChild):
        return []

    def parentContext(self):
        return self.contextFor(self.test)

