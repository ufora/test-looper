import test_looper.server.rendering.Context as Context
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.core.algebraic_to_json as algebraic_to_json
import cgi
import time

octicon = HtmlGeneration.octicon
card = HtmlGeneration.card

class TestRunsContext(Context.Context):
    def __init__(self, renderer, test, options):
        Context.Context.__init__(self, renderer, options)
        self.test = test
        self.commit = test.commitData.commit
        self.repo = self.commit.repo
        self.testName = test.testDefinition.name
        
    def consumePath(self, path):
        if path:
            testRun = self.database.TestRun(path[0])
            if testRun.exists():
                return self.renderer.contextFor(testRun, self.options), path[1:]

        return None, path

    def primaryObject(self):
        return self.test

    def urlBase(self):
        prefix = "repos/" + self.repo.name + "/-/commits/"
        return prefix + self.commit.hash + "/" + self.testName + "/-/runs"

    def renderLink(self, includeCommit=True):
        if includeCommit:
            res = self.contextFor(self.commit).renderLink() + "/"
        else:
            res = ''

        return res + HtmlGeneration.link(self.testName, self.urlString())

    def bootTestOrEnvUrl(self):
        return self.urlString(action="boot")

    def renderPageBody(self):
        test = self.test

        testRuns = self.testManager.database.TestRun.lookupAll(test=test)

        grid = self.gridForTestList_(testRuns)

        testDeps = HtmlGeneration.grid(self.allTestDependencyGrid())

        testDefs = card(
            '<pre class="language-yaml"><code class="line-numbers">%s</code></pre>' % cgi.escape(
                algebraic_to_json.encode_and_dump_as_yaml(test.testDefinition)
                )
            )

        if len(testRuns) == 1:
            extra_tabs = [
                ("Artifacts", self.contextFor(testRuns[0]).artifactsForTestRunGrid(), "artifacts"),
                ("Individual Tests", self.contextFor(testRuns[0]).individualTestReport(), "tests_individual")
                ]
        else:
            extra_tabs = []

        return HtmlGeneration.tabs("test", [
                ("Test Suite Runs", HtmlGeneration.grid(grid), "testruns"), 
                ("Test Dependencies", testDeps, "testdeps"),
                ("Test Definition", testDefs, "testdefs"),
                ] + extra_tabs
                )

    def allTestDependencyGrid(self):
        grid = [["COMMIT", "TEST", ""]]

        for subtest in self.testManager.allTestsDependedOnByTest(self.test):
            grid.append([
                self.contextFor(subtest.commitData.commit).renderLink(),
                self.contextFor(subtest).renderLink(),
                TestSummaryRenderer.TestSummaryRenderer(self, [self.test]).renderSummary()
                ])

        return grid

    def gridForTestList_(self, sortedTests):
        grid = [["TEST", "TYPE", "STATUS", "LOGS", "CLEAR", "STARTED", "ELAPSED (MIN)",
                 "SINCE LAST HEARTBEAT (SEC)", "TOTAL TESTS", "FAILING TESTS"]]

        sortedTests = [x for x in sortedTests if not x.canceled]
        
        for testRun in sortedTests:
            row = []

            row.append(self.renderer.testRunLink(testRun))

            name = testRun.test.testDefinition.name

            row.append(name)

            if testRun.endTimestamp > 0.0:
                row.append("passed" if testRun.success else "failed")
            else:
                row.append(self.renderer.cancelTestRunButton(testRun._identity))

            row.append(self.renderer.testLogsButton(testRun._identity))

            row.append(self.renderer.deleteTestRunButton(testRun._identity))

            row.append(time.ctime(testRun.startedTimestamp))

            if testRun.endTimestamp > 0.0:
                elapsed = (testRun.endTimestamp - testRun.startedTimestamp) / 60.0
            else:
                elapsed = (time.time() - testRun.startedTimestamp) / 60.0

            row.append("%.2f" % elapsed)

            if hasattr(testRun, "lastHeartbeat") and testRun.endTimestamp <= 0.0:
                timeSinceHB = time.time() - testRun.lastHeartbeat
            else:
                timeSinceHB = None

            row.append(str("%.2f" % timeSinceHB) if timeSinceHB is not None else "")

            if testRun.totalTestCount > 0:
                row.append(str(testRun.totalTestCount))
                row.append(str(testRun.totalFailedTestCount))
            else:
                row.append("")
                row.append("")

            grid.append(row)

        return grid

