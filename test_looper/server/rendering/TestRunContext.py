import test_looper.server.rendering.Context as Context
import test_looper.server.rendering.ComboContexts as ComboContexts
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.HtmlGeneration as HtmlGeneration
import logging

card = HtmlGeneration.card

class TestRunContext(Context.Context):
    def __init__(self, renderer, testRun, options):
        Context.Context.__init__(self, renderer, options)
        self.testRun = testRun
        self.test = self.testRun.test
        self.commit = self.test.commitData.commit
        self.repo = self.commit.repo

    def consumePath(self, path):
        return None, path

    def primaryObject(self):
        return self.testRun

    def urlBase(self):
        prefix = "repos/" + self.repo.name + "/-/commits/"

        return prefix + self.commit.hash + "/tests/" + self.test.testDefinition.name + "/-/" + self.testRun._identity

    def renderNavbarLink(self):
        return self.renderLink(includeCommit=False, includeTest=False)

    def renderLink(self, includeCommit=True, includeTest=True):
        res = ""

        if includeCommit:
            res = self.contextFor(self.commit).renderLink()
        
        if includeTest:
            if res:
                res = res + "/"

            res = res + HtmlGeneration.link(self.test.testDefinition.name, self.contextFor(self.test).urlString())

        if res:
            res = res + "/"

        return res + HtmlGeneration.link(self.testRun._identity[:8], self.urlString())

    def renderBreadcrumbPrefixes(self):
        return ["Runs"]

    def contextViews(self):
        if self.test.testDefinition.matches.Build:
            return []
        else:
            return ["artifacts", "tests"]

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
        if self.test.testDefinition.matches.Build:
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

        commit = testRun.test.commitData.commit

        if testRun.test.testDefinition.matches.Build:
            build_key = testRun.test.testDefinition.name.replace("/","_") + ".tar.gz"

            if self.renderer.artifactStorage.build_exists(commit.repo.name, commit.hash, build_key):
                grid.append([
                    HtmlGeneration.link(build_key, self.renderer.buildDownloadUrl(commit.repo.name, commit.hash, build_key)),
                    HtmlGeneration.bytesToHumanSize(self.renderer.artifactStorage.build_size(commit.repo.name, commit.hash, build_key))
                    ])
            else:
                logging.info("No build found at %s", build_key)

        for artifactName, sizeInBytes in self.renderer.artifactStorage.testResultKeysForWithSizes(commit.repo.name, commit.hash, testRun._identity):
            grid.append([
                HtmlGeneration.link(
                    artifactName,
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

