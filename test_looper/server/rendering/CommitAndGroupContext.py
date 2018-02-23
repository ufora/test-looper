import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.rendering.ComboContexts as ComboContexts
import test_looper.server.rendering.CommitContext as CommitContext
import cgi

octicon = HtmlGeneration.octicon
card = HtmlGeneration.card

class CommitAndGroupContext(Context.Context):
    def __init__(self, renderer, commitAndGroup, options):
        Context.Context.__init__(self, renderer, options)
        self.groupName = commitAndGroup.groupName
        self.commit = commitAndGroup.commit

    def renderLink(self):
        return HtmlGeneration.link(self.groupName, self.urlString(), "See individual test results for test group %s" % self.groupName)

    def primaryObject(self):
        return ComboContexts.CommitAndGroup(self.commit, self.groupName)

    def urlBase(self):
        return "repos/" + self.commit.repo.name + "/-/commits/" + self.commit.hash + "/groups/" + self.groupName

    def tests(self):
        res = []
        for t in self.testManager.database.Test.lookupAll(commitData=self.commit.data):
            if self.groupName == TestGridRenderer.TestGridRenderer.groupForTest(t):
                res.append(t)

        return sorted(res, key=lambda t: (0 if t.testDefinition.matches.Build else 1, t.fullname))

    def individualTests(self, test):
        res = {}

        for run in self.database.TestRun.lookupAll(test=test):
            if run.testNames:
                for i in xrange(len(run.testNames.test_names)):
                    cur_runs, cur_successes = res.get(run.testNames.test_names[i], (0,0))

                    cur_runs += 1
                    cur_successes += 1 if run.testFailures[i] else 0

                    res[run.testNames.test_names[i]] = (cur_runs, cur_successes)
        
        return res



    def renderPageBody(self):
        gridForBuilds = self.gridForTests([t for t in self.tests() if t.testDefinition.matches.Build])
        gridForTests = self.gridForTests([t for t in self.tests() if t.testDefinition.matches.Test])

        if not gridForBuilds and not gridForTests:
            return card("No Test Runs")

        headers = ["Suite", "Test", "Status", "Runs"]

        return HtmlGeneration.grid([headers] + gridForBuilds + gridForTests)

    def gridForTests(self, tests):
        grid = []

        for test in tests:
            individualTests = self.individualTests(test)

            if individualTests:
                firstRow = True

                for testName in sorted(individualTests):
                    row = []

                    run_ct, success_ct = individualTests[testName]

                    row.append(self.contextFor(test).renderLink(includeCommit=False) if firstRow else "")
                    row.append(testName)

                    if run_ct == 0:
                        row.append("")
                    elif run_ct == success_ct:
                        row.append(octicon("check"))
                    elif success_ct == 0:
                        row.append(octicon("x"))
                    else:
                        row.append(octicon("alert"))

                    row.append(str(run_ct))

                    firstRow = False

                    grid.append(row)
            else:
                row = []

                row.append(self.contextFor(test).renderLink(includeCommit=False))
                row.append('<span class="text-muted">no individual test data</span>')

                run_ct = 0
                success_ct = 0

                for run in self.database.TestRun.lookupAll(test=test):
                    run_ct += 1
                    if run.success:
                        success_ct += 1

                if run_ct == 0:
                    row.append("")
                elif run_ct == success_ct:
                    row.append(octicon("check"))
                elif success_ct == 0:
                    row.append(octicon("x"))
                else:
                    row.append(octicon("alert"))

                row.append(str(run_ct))

                grid.append(row)

        return grid

    def childContexts(self, currentChild):
        return []

    def parentContext(self):
        return self.contextFor(self.commit)

    def renderMenuItemText(self):
        return self.groupName
