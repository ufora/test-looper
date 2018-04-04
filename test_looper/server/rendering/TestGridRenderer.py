import test_looper.server.rendering.Context as Context
import test_looper.data_model.TestManager as TestManager
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer


class TestGridRenderer:
    def __init__(self, rows, testsForRowFun, 
            headerLinkFun = lambda group: "", 
            cellLinkFun = lambda group, row: '', 
            groupFun = lambda test: TestManager.TestManager.configurationForTest(test)
            ):
        self.rows = rows
        self.headerLinkFun = headerLinkFun
        self.testsForRowFun = testsForRowFun
        self.cellLinkFun = cellLinkFun
        self.groupFun = groupFun
        self.groups = set()

        for r in rows:
            for t in self.testsForRowFun(r):
                if t.testDefinitionSummary.type != "Deployment":
                    self.groups.add(self.groupFun(t))

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

    def gridRow(self, row):
        groupMap = {g:[] for g in self.groups}

        for t in self.testsForRowFun(row):
            if t.testDefinitionSummary.type != "Deployment":
                groupMap[self.groupFun(t)].append(t)

        return [
            TestSummaryRenderer.TestSummaryRenderer(
                    groupMap.get(g,[]),
                    testSummaryUrl=self.cellLinkFun(group=g,row=row) if groupMap.get(g,[]) else ""
                    ).renderSummary()
                for g in sorted(self.groups)
            ]
