import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer


class TestGridRenderer:
    def __init__(self, rows, testsForRowFun):
        self.rows = rows
        self.testsForRowFun = testsForRowFun

        self.groups = set()

        for r in rows:
            for t in self.testsForRowFun(r):
                self.groups.add(self.groupForTest(t))

    def headers(self):
        return sorted(self.groups)

    def grid(self):
        return [self.gridRow(r) for r in self.rows]

    def gridRow(self, row):
        groupMap = {g:[] for g in self.groups}

        for t in self.testsForRowFun(row):
            groupMap[self.groupForTest(t)].append(t)

        return [
            TestSummaryRenderer.TestSummaryRenderer(groupMap[g]).renderSummary()
                if groupMap[g] else "" for g in sorted(self.groups)
            ]

    def groupForTest(self, test):
        if test.testDefinition.displayGroup:
            return test.testDefinition.displayGroup
        else:
            return test.testDefinition.environment_name

