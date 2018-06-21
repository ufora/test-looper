import test_looper.server.rendering.Context as Context
import test_looper.data_model.TestManager as TestManager
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import time
import logging

class TestGridRenderer:
    def __init__(self, rows, testsForRowFun, 
            headerLinkFun = lambda group: "", 
            cellLinkFun = lambda group, row: '', 
            groupFun = lambda test: TestManager.TestManager.configurationForTest(test),
            cacheName = None,
            database = None
            ):
        self.rows = rows
        self.headerLinkFun = headerLinkFun
        self.testsForRowFun = testsForRowFun
        self.cellLinkFun = cellLinkFun
        self.groupFun = groupFun
        self.groups = set()
        self.cacheName = cacheName
        self.database = database

        if self.cacheName is not None:
            self.database.addCalculationCache((self.cacheName, "cells"), self.calculateCellContents)
            self.database.addCalculationCache((self.cacheName, "rows"), self.calculateGroupsForRow)

        if self.cacheName is None:
            for r in rows:
                for t in self.testsForRowFun(r):
                    if t.testDefinitionSummary.type != "Deployment":
                        self.groups.add(self.groupFun(t))
        else:
            for r in rows:
                self.groups.update(
                    self.database.lookupCachedCalculation((self.cacheName, "rows"), (r,))
                    )

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

    def calculateGroupsForRow(self, row):
        groups = set()
        for t in self.testsForRowFun(row):
            if t.testDefinitionSummary.type != "Deployment":
                groups.add(self.groupFun(t))

        return groups


    def calculateCellContents(self, group, row):
        logging.info("Recalculating cell contents for %s, %s", group, row)

        tests = []
        for t in self.testsForRowFun(row):
            if t.testDefinitionSummary.type != "Deployment" and self.groupFun(t) == group:
                tests.append(t)

        return TestSummaryRenderer.TestSummaryRenderer(
                    tests,
                    testSummaryUrl=self.cellLinkFun(group=group,row=row) if tests else ""
                    ).renderSummary()

    def gridRow(self, row):
        if self.cacheName:
            return [self.database.lookupCachedCalculation((self.cacheName, "cells"), (g,row)) for g in self.groups]
        else:
            return [self.calculateCellContents(g, row) for g in sorted(self.groups)]
