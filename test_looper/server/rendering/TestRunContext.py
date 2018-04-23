import test_looper.server.rendering.Context as Context
import test_looper.server.rendering.ComboContexts as ComboContexts
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.rendering.IndividualTestGridRenderer as IndividualTestGridRenderer
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.core.ArtifactStorage as ArtifactStorage
import time
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
        while path and path[0] == "-":
            path = path[1:]

        if path and path[0] == "individualTest":
            return self.contextFor(ComboContexts.IndividualTest(self.testRun, "/".join(path[1:]))), []

        return None, path

    def renderIndividualTestResults(self):
        #show broken out tests over the last N commits
        rows = [self.testRun]

        def rowLinkFun(row):
            return self.contextFor(row).renderLink(includeCommit=False, includeTest=False)

        def testFun(row):
            return [row]

        def cellUrlFun(testGroup, row):
            return None

        def rowContextFun(row):
            return row

        renderer = IndividualTestGridRenderer.IndividualTestGridRenderer(
            rows,
            self, 
            testFun,
            cellUrlFun,
            rowContextFun
            )

        grid = [["Test Run", "Logs", "Elapsed (Min)", "Status", ""] + renderer.headers()]

        for testRun in rows:
            row = [rowLinkFun(testRun),self.renderer.testLogsButton(testRun._identity)]

            if testRun.endTimestamp > 0.0:
                elapsed = (testRun.endTimestamp - testRun.startedTimestamp) / 60.0
            else:
                elapsed = (time.time() - testRun.startedTimestamp) / 60.0

            row.append("%.2f" % elapsed)

            if testRun.endTimestamp > 0.0:
                row.append("passed" if testRun.success else "failed")
            else:
                row.append("running")

            row.append("&nbsp;")

            grid.append(row + renderer.gridRow(testRun))

        grid = HtmlGeneration.transposeGrid(grid)

        return HtmlGeneration.grid(grid, dataTables=True, header_rows=5)

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
            return self.renderIndividualTestResults()
        
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

