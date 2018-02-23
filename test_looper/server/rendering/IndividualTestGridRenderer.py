import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.rendering.ComboContexts as ComboContexts
import cgi

octicon = HtmlGeneration.octicon

def glomTogether(list):
    """Given a list of things, return a list of tuples (item, count) collapsing successive tuples."""
    cur = None
    count = None

    res = []

    for i in xrange(len(list)+1):
        if i == 0 or i == len(list) or list[i] != cur:
            if i != 0:
                res.append((cur,count))
            
            if i != len(list):
                cur = list[i]
                count = 1
        else:
            count += 1
    return res


class IndividualTestGridRenderer:
    def __init__(self, rows, parentContext, testsForRowFun):
        self.rows = rows
        self.parentContext = parentContext
        self.database = parentContext.database
        self.testsForRowFun = testsForRowFun
        self.testsByName = set()

        for r in rows:
            for t in self.individualTestsForRowFun(r):
                self.testsByName.add(t)

    def individualTestsForRowFun(self, row):
        res = {}
        for t in self.testsForRowFun(row):
            for run in self.database.TestRun.lookupAll(test=t):
                if run.testNames:
                    testNames = run.testNames.test_names
                    testFailures = run.testFailures
                    testHasLogs = run.testHasLogs
                    
                    for i in xrange(len(testNames)):
                        cur_runs, cur_successes, url = res.get(testNames[i], (0,0,""))

                        cur_runs += 1
                        cur_successes += 1 if testFailures[i] else 0

                        if testHasLogs and testHasLogs[i] and not url:
                            url = self.parentContext.contextFor(ComboContexts.IndividualTest(t, testNames[i])).urlString()

                        res[testNames[i]] = (cur_runs, cur_successes, url)
        return res

    @property
    def cellWidth(self):
        if len(self.testsByName) > 40:
            return 5
        else:
            return 20

    def headers(self):
        headers = []
        for header, count in glomTogether([self.subgroupForIndividualTestName(x) for x in sorted(self.testsByName)]):
            headers.append(
                '<div style="display: inline-block; width: {width}px; text-align:center">{header}</div>'.format(
                    width=(count+1)*self.cellWidth,
                    header=header
                    )
                )

        return ["Builds", "Tests", "".join(headers)]

    def grid(self):
        return [self.gridRow(r) for r in self.rows]

    def subgroupForIndividualTestName(self, testName):
        return testName.split("::",1)[0]

    def gridRow(self, row, urlFun = lambda group,row: ""):
        testResults = self.individualTestsForRowFun(row)

        res = []
        lastH = None
        for h in sorted(self.testsByName):
            if h in testResults:
                run_count, success_count, url = testResults[h]

                if run_count == success_count:
                    type = "test-result-cell-success"
                    tooltip = "Test %s succeeded" % h
                    if run_count > 1:
                        tooltip += " over %s runs" % run_count
                elif success_count:
                    type = "test-result-cell-partial"
                    tooltip = "Test %s succeeded %s / %s times" % (h, success_count, run_count)
                else:
                    type = "test-result-cell-fail"
                    tooltip = "Test %s failed" % h
                    if run_count > 1:
                        tooltip += " over %s runs" % run_count
            else:
                url = ""
                type = "test-result-cell-notrun"
                tooltip = "Test %s didn't run" % h

            if lastH is not None and self.subgroupForIndividualTestName(h) != self.subgroupForIndividualTestName(lastH):
                res.append('<div style="display: inline-block; width: {width}px" class="test-result-cell-notrun"></div>'.format(width=self.cellWidth))

            res.append('<div {onclick} class="test-result-cell{sm} {type}" data-toggle="tooltip" title="{text}">&nbsp;</div>'.format(
                type=type,
                text=cgi.escape(tooltip),
                sm="-sm" if len(self.testsByName) > 40 else "",
                onclick='onclick="location.href=\'{url}\'"'.format(url=url) if url else ''
                ))

            lastH = h

        builds = [x for x in self.testsForRowFun(row) if x.testDefinition.matches.Build]
        tests = [x for x in self.testsForRowFun(row) if x.testDefinition.matches.Test]
        
        return [
            TestSummaryRenderer.TestSummaryRenderer(builds).renderSummary(),
            TestSummaryRenderer.TestSummaryRenderer(tests).renderSummary(),
            {"content": "".join(res), "class": "nopadding"}
            ]




