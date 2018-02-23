import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.rendering.ComboContexts as ComboContexts
import test_looper.core.PrefixTree as PrefixTree
import cgi
import os

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

        #compute a set of spanning prefixes that gets the number of groups down below 40
        if self.testsByName:
            self.prefixTree = PrefixTree.PrefixTree(self.testsByName)
            self.prefixTree.balance(80)
            self.prefixesToStrings = self.prefixTree.stringsAndPrefixes()

            print self.prefixesToStrings.keys()
        else:
            self.prefixesToStrings = {}

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

    def cellWidth(self, prefix):
        if len(self.prefixesToStrings[prefix]) == 1:
            return 10
        if len(self.prefixesToStrings) < 16:
            return 100
        else:
            return 40

    def cellType(self, prefix):
        if len(self.prefixesToStrings[prefix]) == 1:
            return "test-result-cell-sm"
        if len(self.prefixesToStrings) < 16:
            return "test-result-cell-lg"
        else:
            return "test-result-cell"

    def cellHeader(self, prefix):
        if len(self.prefixesToStrings[prefix]) == 1:
            return "()"
        if len(self.prefixesToStrings) < 16:
            return prefix
        else:
            return "()"

    def cellTitle(self, prefix):
        if len(self.prefixesToStrings[prefix]) == 1:
            return "Results for test %s" % prefix
        
        return "Results for %s tests starting with %s" % (len(self.prefixesToStrings[prefix]), prefix)
       

    def headers(self):
        headers = []
        for prefix in self.prefixesToStrings:
            headers.append(
                '<div style="display: inline-block; width: {width}px; text-align:center; overflow-x: hidden;" data-toggle="tooltip" title="{title}">{contents}</div>'.format(
                    width=self.cellWidth(prefix),
                    contents=self.cellHeader(prefix),
                    title=self.cellTitle(prefix)
                    )
                )

        return ["Builds", "Tests", {"content": "".join(headers), "class": "nopadding"}]

    def grid(self):
        return [self.gridRow(r) for r in self.rows]

    def subgroupForIndividualTestName(self, testName):
        return testName.split("::",1)[0]

    def gridRow(self, row, urlFun = lambda group,row: ""):
        testResults = self.individualTestsForRowFun(row)

        def resultsForPrefix(prefix):
            bad_count, flakey_count, good_count, not_running_count = 0,0,0,0

            for testName in self.prefixesToStrings[prefix]:
                if testName not in testResults:
                    not_running_count += 1
                else:
                    this_runs, this_successes, this_url = testResults[testName]
                    
                    if this_runs == this_successes:
                        good_count += 1
                    elif this_successes == 0:
                        bad_count += 1
                    else:
                        flakey_count += 1

            return bad_count, flakey_count, good_count, not_running_count

        res = []
        lastH = None
        for prefix in sorted(self.prefixesToStrings):
            if len(self.prefixesToStrings[prefix]) == 1:
                contents = "&nbsp;"

                testName = self.prefixesToStrings[prefix][0]
                
                if testName in testResults:
                    run_count, success_count, url = self.testResults[testName]

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
            else:
                bad_count, flakey_count, good_count, not_running_count = resultsForPrefix(prefix)

                contents = "%s/%s/%s/%s" % (bad_count, flakey_count, good_count, not_running_count)
                type = "test-result-cell-notrun"
                tooltip = ""
                url = ""

            res.append('<div {onclick} class="{celltype} {type}" data-toggle="tooltip" title="{text}">{contents}</div>'.format(
                type=type,
                celltype=self.cellType(prefix),
                contents=contents,
                text=cgi.escape(tooltip),
                onclick='onclick="location.href=\'{url}\'"'.format(url=url) if url else ''
                ))

        builds = [x for x in self.testsForRowFun(row) if x.testDefinition.matches.Build]
        tests = [x for x in self.testsForRowFun(row) if x.testDefinition.matches.Test]
        
        return [
            TestSummaryRenderer.TestSummaryRenderer(builds).renderSummary(),
            TestSummaryRenderer.TestSummaryRenderer(tests).renderSummary(),
            {"content": "".join(res), "class": "nopadding"}
            ]




