import test_looper.server.rendering.Context as Context
import test_looper.data_model.TestManager as TestManager
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer


class TestGridRenderer:
    def __init__(self, rows, testsForRowFun, headerLinkFun = lambda group: None):
        self.rows = rows
        self.headerLinkFun = headerLinkFun
        self.testsForRowFun = testsForRowFun

        self.groups = set()

        for r in rows:
            for t in self.testsForRowFun(r):
                self.groups.add(TestManager.TestManager.configurationForTest(t))

    def headers(self):
        if not self.headerLinkFun:
            return sorted(self.groups)
        else:
            res = []
            for g in sorted(self.groups):
                link = self.headerLinkFun(g)
                if link:
                    res.append(link)
                else:
                    res.append(g)
            
            return res

    def grid(self):
        return [self.gridRow(r) for r in self.rows]

    def gridRow(self, row, urlFun = lambda group,row: ""):
        groupMap = {g:[] for g in self.groups}

        for t in self.testsForRowFun(row):
            groupMap[TestManager.TestManager.configurationForTest(t)].append(t)

        return [
            TestSummaryRenderer.TestSummaryRenderer(groupMap[g],testSummaryUrl=urlFun(group=g,row=row)).renderSummary()
                if groupMap[g] else "" for g in sorted(self.groups)
            ]
