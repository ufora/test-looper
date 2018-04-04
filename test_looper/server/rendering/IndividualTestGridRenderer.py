import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.rendering.ComboContexts as ComboContexts
import cgi
import os

octicon = HtmlGeneration.octicon

def groupBy(things, groupFun):
    result = {}
    for t in things:
        g = groupFun(t)
        if g not in result:
            result[g] = []
        result[g].append(t)
    return {g: sorted(result[g]) for g in result}

class IndividualTest:
    def __init__(self, testSuiteName, testName):
        self.testSuiteName = testSuiteName
        self.testName = testName

        if "::" in self.testName:
            self.group = self.testName[:self.testName.find("::")]
            self.name = self.testName[self.testName.find("::")+2:]
        else:
            self.group = "/".join(testSuiteName.split("/")[:-1])
            if ":" in self.group:
                self.group = self.group.split(":")[-1]

            self.name = self.testName

    def __cmp__(self, other):
        return cmp((self.group, self.name), (other.group, other.name))

    def __hash__(self):
        return hash((self.name, self.group))

class IndividualTestGridRenderer:
    def __init__(self, rows, parentContext, testsForRowFun, cellUrlFun=lambda group, row: "", 
            breakOutIndividualTests=False
            ):
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

        self.breakOutIndividualTests = breakOutIndividualTests
        
    def placeTestsIntoGroups(self):
        self.testsByName = set()

        for r in self.rows:
            for t in self.individualTestsForRowFun(r):
                self.testsByName.add(t)

        return groupBy(self.testsByName, lambda t: t.group)

    def headers(self):
        headers = []
        if self.breakOutIndividualTests:
            for test in sorted(sum(self.groupsToTests.values(), [])):
                headers.append(test.testName)
        else:
            for group in sorted(self.groupsToTests):
                headers.append(
                    '<a href="{url}" data-toggle="tooltip" title="{title}">{contents}</a>'.format(
                        contents=group,
                        title=self.groupTitle(group),
                        url=self.parentContext.withOptions(testGroup=group).urlString()
                        )
                    )

        return headers

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
                    testSuiteName = run.test.testDefinitionSummary.name
                    
                    for i in xrange(len(testNames)):
                        cur_runs, cur_successes, testIfHasLogs = res.get(testNames[i], (0,0,None))

                        cur_runs += 1
                        cur_successes += 1 if testFailures[i] else 0

                        if testHasLogs and testHasLogs[i] and not testIfHasLogs:
                            testIfHasLogs = t

                        res[IndividualTest(testSuiteName, testNames[i])] = (cur_runs, cur_successes, testIfHasLogs)
        
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
                        run_count, success_count, testIfHasLogs = testResults[individualTest]

                        if testIfHasLogs:
                            url = self.parentContext.contextFor(
                                ComboContexts.IndividualTest(testIfHasLogs, individualTest.testName)
                                ).urlString()
                        else:
                            url = ""

                        if run_count == success_count:
                            cellClass = "test-result-cell-success"
                            tooltip = "Test %s succeeded" % individualTest.name
                            if run_count > 1:
                                tooltip += " over %s runs" % run_count
                            contentsDetail=octicon("check")

                        elif success_count:
                            cellClass = "test-result-cell-partial"
                            tooltip = "Test %s succeeded %s / %s times" % (individualTest.name, success_count, run_count)
                            contentsDetail=octicon("alert")
                        else:
                            cellClass = "test-result-cell-fail"
                            tooltip = "Test %s failed" % individualTest.name
                            if run_count > 1:
                                tooltip += " over %s runs" % run_count
                            contentsDetail=octicon("x")
                    else:
                        url = ""
                        cellClass = "test-result-cell-notrun"
                        tooltip = "Test %s didn't run" % individualTest.name
                        contentsDetail = ""

                    gridRow.append({'content':
                        '<div {onclick} data-toggle="tooltip" title="{text}">{contents}</div>'.format(
                            contents=contentsDetail,
                            text=cgi.escape(tooltip),
                            onclick='onclick="location.href=\'{url}\'"'.format(url=url) if url else ''
                            ),
                        "class": cellClass
                        }
                        )
            else:
                bad,flakey,good,not_running = aggregatedResultsForGroup(group)

                url = self.cellUrlFun(group, row)

                if bad+flakey+good:
                    interior = '<span class="text-danger">%s</span> / %s' % (bad+flakey, bad+flakey+good)
                else:
                    interior = ""

                if url:
                    gridRow.append('<div onclick="location.href=\'%s\'" class="clickable-div">%s</div>' % (url, interior))
                else:
                    gridRow.append(interior)

        return gridRow




