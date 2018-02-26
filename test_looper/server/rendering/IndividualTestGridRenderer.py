import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.rendering.ComboContexts as ComboContexts
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

def groupBy(things, groupFun):
    result = {}
    for t in things:
        g = groupFun(t)
        if g not in result:
            result[g] = []
        result[g].append(t)
    return result

CELL_WIDTH = 10

class IndividualTest:
    def __init__(self, testSuite, testName):
        self.testSuite = testSuite
        self.testName = testName

    def group(self):
        if "::" in self.testName:
            return self.testName[:self.testName.find("::")]
        else:
            return "/".join(self.testSuite.testDefinition.name.split("/")[:-1])

    def name(self):
        if "::" in self.testName:
            return self.testName[self.testName.find("::")+2:]
        else:
            return self.testName

    def __cmp__(self, other):
        return cmp((self.group(), self.name()), (other.group(), other.name()))

    def __hash__(self):
        return hash((self.name(), self.group()))

class IndividualTestGridRenderer:
    def __init__(self, rows, parentContext, testsForRowFun, cellUrlFun=lambda group, row: ""):
        self.rows = rows
        self.cellUrlFun = cellUrlFun
        self.parentContext = parentContext
        self.database = parentContext.database
        self.testsForRowFun = testsForRowFun
        
        self.groupsToTests = self.placeTestsIntoGroups()

        if parentContext.options.get("testGroup"):
            group = parentContext.options.get("testGroup")
            self.groupsToTests = {group: self.groupsToTests.get(group,[])}
        
        self.totalTestsToDisplay = sum([len(x) for x in self.groupsToTests.values()])

        if self.totalTestsToDisplay < 200 and len(self.groupsToTests) == 1:
            self.breakOutIndividualTests = True
        else:
            self.breakOutIndividualTests = False

    def placeTestsIntoGroups(self):
        self.testsByName = set()

        for r in self.rows:
            for t in self.individualTestsForRowFun(r):
                self.testsByName.add(t)

        return groupBy(self.testsByName, lambda t: t.group())

    def headers(self):
        headers = []
        if self.parentContext.options.get("testGroup"):
            headers.append("")
        else:
            for group in sorted(self.groupsToTests):
                headers.append(
                    '<a href="{url}" data-toggle="tooltip" title="{title}">{contents}</a>'.format(
                        contents=group,
                        title=self.groupTitle(group),
                        url=self.parentContext.withOptions(testGroup=group).urlString()
                        )
                    )

        return ["Builds", "Tests"] + headers

    def groupTitle(self, group):
        return "Results for %s tests in group %s" % (len(self.groupsToTests[group]), group)

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

                        res[IndividualTest(run.test, testNames[i])] = (cur_runs, cur_successes, url)
        
        return res

    def grid(self):
        return [self.gridRow(r) for r in self.rows]

    def gridRow(self, row):
        testResults = self.individualTestsForRowFun(row)

        gridRow = []
        
        def aggregatedResultsForGroup(group):
            bad_count, flakey_count, good_count, not_running_count = 0,0,0,0

            for individualTest in self.groupsToTests[group]:
                if individualTest not in testResults:
                    not_running_count += 1
                else:
                    this_runs, this_successes, this_url = testResults[individualTest]
                    
                    if this_runs == this_successes:
                        good_count += 1
                    elif this_successes == 0:
                        bad_count += 1
                    else:
                        flakey_count += 1

            return bad_count, flakey_count, good_count, not_running_count

        for group in sorted(self.groupsToTests):
            if self.breakOutIndividualTests:
                res = []
                for individualTest in self.groupsToTests[group]:
                    if individualTest in testResults:
                        run_count, success_count, url = testResults[individualTest]

                        if run_count == success_count:
                            type = "test-result-cell-success"
                            tooltip = "Test %s succeeded" % individualTest.name()
                            if run_count > 1:
                                tooltip += " over %s runs" % run_count
                        elif success_count:
                            type = "test-result-cell-partial"
                            tooltip = "Test %s succeeded %s / %s times" % (individualTest.name(), success_count, run_count)
                        else:
                            type = "test-result-cell-fail"
                            tooltip = "Test %s failed" % individualTest.name()
                            if run_count > 1:
                                tooltip += " over %s runs" % run_count
                    else:
                        url = ""
                        type = "test-result-cell-notrun"
                        tooltip = "Test %s didn't run" % individualTest.name()

                    res.append('<div {onclick} class="{celltype} {type}" data-toggle="tooltip" title="{text}">{contents}</div>'.format(
                        type=type,
                        celltype="test-result-cell",
                        contents="&nbsp;",
                        text=cgi.escape(tooltip),
                        onclick='onclick="location.href=\'{url}\'"'.format(url=url) if url else ''
                        ))
                gridRow.append({"content": "".join(res), "class": "nopadding"})
            else:
                bad,flakey,good,not_running = aggregatedResultsForGroup(group)

                url = self.cellUrlFun(group, row)

                if url:
                    gridRow.append('<div onclick="location.href=\'%s\'" class="clickable-div"><span class="text-danger">%s</span> / %s</div>' % (url, bad+flakey, bad+flakey+good))
                else:
                    gridRow.append('<span class="text-danger">%s</span> / %s' % (bad+flakey, bad+flakey+good))

        builds = [x for x in self.testsForRowFun(row) if x.testDefinition.matches.Build]
        tests = [x for x in self.testsForRowFun(row) if x.testDefinition.matches.Test]
        
        return [
            TestSummaryRenderer.TestSummaryRenderer(builds).renderSummary(),
            TestSummaryRenderer.TestSummaryRenderer(tests).renderSummary()
            ] + gridRow




