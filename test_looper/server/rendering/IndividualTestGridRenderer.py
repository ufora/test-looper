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

class IndividualTestGridRenderer:
    def __init__(self, rows, parentContext, testsForRowFun, cellUrlFun=lambda group, row: "", individualTestRunContextFor=lambda row: None):
        self.rows = rows
        self.cellUrlFun = cellUrlFun
        self.parentContext = parentContext
        self.database = parentContext.database
        self.testsForRowFun = testsForRowFun
        self.individualTestRunContextFor = individualTestRunContextFor
        
        self.groupsToTests = self.placeTestsIntoGroups()

        self.totalTestsToDisplay = sum([len(x) for x in self.groupsToTests.values()])
        
    def placeTestsIntoGroups(self):
        self.testsByName = set()

        for r in self.rows:
            for t in self.individualTestsForRowFun(r):
                self.testsByName.add(t)

        return groupBy(self.testsByName, lambda t: t.split(".")[0])

    def headers(self):
        headers = []
        for test in self.individualTestNames():
            headers.append(test)
    
        return headers

    def individualTestNames(self):
        return sorted(sum(self.groupsToTests.values(), []))

    def groupTitle(self, group):
        return "Results for %s tests in group %s" % (len(self.groupsToTests[group]), group)

    def individualTestsForRowFun(self, row):
        res = {}
        for t in self.testsForRowFun(row):
            if isinstance(t, self.database.Test):
                runs = list(self.database.TestRun.lookupAll(test=t))
            elif isinstance(t, self.database.TestRun):
                runs = [t]
            else:
                assert False, "Can't handle %s" % t

            for run in runs:
                if run.testNames:
                    testNames = run.testNames.test_names
                    testNameIndices = run.testStepNameIndex
                    testSucceeded = run.testStepSucceeded
                    testHasLogs = run.testStepHasLogs
                    testSuiteName = run.test.testDefinitionSummary.name

                    for i in xrange(len(run.testStepNameIndex)):
                        cur_runs, cur_successes, testIfHasLogs = res.get(testNames[testNameIndices[i]], (0,0,None))

                        cur_runs += 1
                        cur_successes += 1 if testSucceeded[i] else 0

                        if testHasLogs and testHasLogs[i] and not testIfHasLogs:
                            testIfHasLogs = run.test

                        res[testNames[testNameIndices[i]]] = (cur_runs, cur_successes, testIfHasLogs)
        
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

        for individualTest in self.individualTestNames():
            urlIsDropdown = False

            if individualTest in testResults:
                run_count, success_count, testIfHasLogs = testResults[individualTest]

                if testIfHasLogs:
                    context = self.individualTestRunContextFor(row)
                    if context:
                        url = self.parentContext.contextFor(
                            ComboContexts.IndividualTest(context, individualTest)
                            ).urlString()
                    else:
                        url = ""
                else:
                    url = ""

                if run_count == success_count:
                    cellClass = "test-result-cell-success"
                    tooltip = "Test %s succeeded" % individualTest
                    if run_count > 1:
                        tooltip += " over %s runs" % run_count
                    contentsDetail="OK"

                    if run_count > 1:
                        contentsDetail += " (%s runs)" % run_count

                elif success_count:
                    cellClass = "test-result-cell-partial"
                    tooltip = "Test %s succeeded %s / %s times" % (individualTest, success_count, run_count)
                    contentsDetail="FLAKEY (%s/%s runs failed)" % (run_count-success_count, run_count)
                else:
                    cellClass = "test-result-cell-fail"
                    tooltip = "Test %s failed" % individualTest
                    if run_count > 1:
                        tooltip += " over %s runs" % run_count
                    contentsDetail="FAIL"

                    if run_count > 1:
                        contentsDetail += " (%s runs)" % run_count

            else:
                url = ""
                cellClass = "test-result-cell-notrun"
                tooltip = "Test %s didn't run" % individualTest
                contentsDetail = ""

            if urlIsDropdown:
                contentsDetail = '<span style="width:100px">%s</span>' % contentsDetail + HtmlGeneration.urlDropdown("", url)

            gridRow.append({'content':
                '<div {onclick} data-toggle="tooltip" title="{text}">{contents}</div>'.format(
                    contents=contentsDetail,
                    text=cgi.escape(tooltip),
                    onclick='onclick="window.open(\'{url}\',\'_blank\')"'.format(url=url) if url and not urlIsDropdown else ''
                    ),
                "class": cellClass
                }
                )
        return gridRow




