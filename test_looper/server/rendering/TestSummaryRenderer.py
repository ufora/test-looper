import test_looper.server.HtmlGeneration as HtmlGeneration
import cgi

octicon = HtmlGeneration.octicon


def convertToIntIfClose(x):
    if abs(x - round(x, 0)) < .01:
        return int(round(x, 0))
    return x



def cached(f):
    def function(self):
        cname = '_cache' + f.__name__
        if cname in self.__dict__:
            return self.__dict__[cname]
        else:
            self.__dict__[cname] = f(self)
        return self.__dict__[cname]

    return function

class TestSummaryRenderer:
    """Class for rendering a specific set of tests."""
    def __init__(self, tests, testSummaryUrl=None, ignoreIndividualTests=False):
        self.tests = tests
        self.ignoreIndividualTests = ignoreIndividualTests
        self.url = testSummaryUrl

    @cached
    def allBuilds(self):
        return [t for t in self.tests if t.testDefinitionSummary.type == "Build"]

    @cached
    def allTests(self):
        return [t for t in self.tests if t.testDefinitionSummary.type == "Test"]

    def renderSummary(self):
        #first, see whether we have any tests
        activeTests = sum(t.activeRuns for t in self.allTests())
        activeBuilds = sum(t.activeRuns for t in self.allBuilds())
        active = activeTests + activeBuilds

        if not self.tests:
            button_text = '<span class="text-muted" style="width:30px">&nbsp;</span>' 
        else:
            button_text = self.renderButtonContents(active)

        if activeTests:
            button_text = button_text + ("&nbsp;" if button_text else "") + (
                '<span class="badge badge-info pl-1">{workers}{icon}</span>'.format(workers=max(activeTests,0), icon=octicon("pulse"))
                )
        if activeBuilds:
            button_text = button_text + ("&nbsp;" if button_text else "") + (
                '<span class="badge badge-info pl-1">{workers}{icon}</span>'.format(workers=max(activeTests,0), icon=octicon("tools"))
                )

        summary = self.tooltipSummary()

        if summary:
            summary = "<span>%s</span>" % summary

        if active:
            if summary:
                summary += "&nbsp;"

            summary += "<span>%s active jobs</span>" % active

        if summary:
            if self.url:
                button_text = (
                    '<div onclick="location.href=\'{url}\';" class="clickable-div" data-toggle="tooltip" title="{summary}" data-html="true">{text}</div>'
                        .format(summary=cgi.escape(summary), text=button_text,url=self.url)
                    )
            else:
                button_text = (
                    '<span data-toggle="tooltip" title="{summary}" data-html="true">{text}</span>'
                        .format(summary=cgi.escape(summary), text=button_text)
                    )

        elif self.url:
            button_text = (
                '<div onclick="location.href=\'{url}\';" class="clickable-div" title="{summary}" data-html="true">{text}</div>'
                    .format(summary=cgi.escape(summary), text=button_text,url=self.url)
                )
        
        return button_text


    def categorizeAllBuilds(self):
        goodBuilds = []
        badBuilds = []
        waitingBuilds = []

        builds = self.allBuilds()
        for b in builds:
            category = self.categorizeBuild(b)
            if category == "OK":
                goodBuilds += [b]
            if category == "BAD":
                badBuilds += [b]
            if category == "PENDING":
                waitingBuilds += [b]

        return goodBuilds,badBuilds,waitingBuilds

    def tooltipSummary(self):
        #first, see if all of our builds have completed
        goodBuilds,badBuilds,waitingBuilds = self.categorizeAllBuilds()

        runningBuilds = [b for b in waitingBuilds if b.activeRuns]
        waitingBuilds = [b for b in waitingBuilds if not b.activeRuns]
        
        res = ""
        if badBuilds:
            res += "<div>%s builds failed</div>" % (len(badBuilds))
        if goodBuilds:
            res += "<div>%s builds succeeded</div>" % (len(goodBuilds))
        if runningBuilds:
            res += "<div>%s builds running</div>" % (len(runningBuilds))
        if waitingBuilds:
            if waitingBuilds[0].calculatedPriority == 0:
                res += "<div>%s builds waiting, but the commit is not prioritized</div>" % (len(waitingBuilds))
            else:
                res += "<div>%s builds waiting</div>" % (len(waitingBuilds))

        tests = self.allTests()

        if not tests:
            if not self.allBuilds():
                return "No tests or builds defined."
            return res

        suitesNotRun = 0
        suitesNotRunAndNotPrioritized = 0
        
        totalTests = 0
        suitesDepFailed = 0
        suitesSucceeded = 0
        suitesFailed = 0
        suitesRunning = 0
        totalFailedTestCount = 0
        suitesWithNoIndividualTests = 0

        for t in tests:
            if t.priority.matches.DependencyFailed:
                suitesDepFailed += 1
            elif t.totalRuns == 0:
                if t.calculatedPriority == 0:
                    suitesNotRunAndNotPrioritized += 1
                else:
                    if t.activeRuns:
                        suitesRunning += 1
                    else:
                        suitesNotRun += 1
            elif t.successes == 0:
                suitesFailed += 1
            else:
                if t.totalTestCount == 0:
                    suitesWithNoIndividualTests += 1
                else:
                    suitesSucceeded += 1

                totalTests += t.totalTestCount / t.totalRuns if t.totalRuns != 1 else t.totalTestCount
                totalFailedTestCount += t.totalFailedTestCount / t.totalRuns if t.totalRuns != 1 else t.totalFailedTestCount

        if suitesWithNoIndividualTests:
            res += "<div>%s test suites succeeded but dumped no individual tests</div>" % suitesWithNoIndividualTests

        if suitesDepFailed:
            res += "<div>%s test suites had failed dependencies</div>" % suitesDepFailed
        
        if suitesRunning:
            res += "<div>%s test suites are actively running</div>" % suitesRunning

        if suitesNotRun:
            res += "<div>%s test suites are waiting to run</div>" % suitesNotRun
        
        if suitesNotRunAndNotPrioritized:
            res += "<div>%s test suites are waiting to run but are not prioritized</div>" % suitesNotRunAndNotPrioritized

        if suitesSucceeded:
            res += "<div>%s test suites ran</div>" % suitesSucceeded

        if suitesFailed:
            res += "<div>%s test suites failed</div>" % suitesFailed

        totalTests = convertToIntIfClose(totalTests)
        totalFailedTestCount = convertToIntIfClose(totalFailedTestCount)

        if totalTests:
            res += "<div>%s / %s individual test runs failed.</div>" % (
                totalFailedTestCount,
                totalTests
                )

        return res

    def categorizeBuild(self, b):
        if b.successes > 0:
            return "OK"
        if b.priority.matches.WaitingToRetry:
            return "PENDING"
        if b.priority.matches.DependencyFailed or b.totalRuns > 0:
            return "BAD"
        return "PENDING"

    def renderButtonContents(self, activeCount):
        #first, see if all of our builds have completed
        goodBuilds,badBuilds,waitingBuilds = self.categorizeAllBuilds()
        tests = self.allTests()


        totalTests = 0
        totalFailedTestCount = 0

        suitesNotRun = 0
        depFailed = 0
        for t in tests:
            if t.priority.matches.DependencyFailed:
                depFailed += 1
            elif t.totalRuns == 0:
                suitesNotRun += 1
            else:
                totalTests += t.totalTestCount / t.totalRuns
                totalFailedTestCount += t.totalFailedTestCount / t.totalRuns

        build_summary = ""
        allBuildsGood = False

        if badBuilds:
            build_summary = """<span class="text-danger">%s</span>""" % octicon("x")
        elif len(waitingBuilds):
            if activeCount:
                return ""
            if waitingBuilds[0].calculatedPriority == 0:
                build_summary = '<span class="text-muted">%s</span>' % "..."
            else:
                build_summary = octicon("watch")
        else:
            build_summary = octicon("check")
            allBuildsGood = True

        if not tests:
            #we have no tests, but the builds passed
            return build_summary

        if totalTests == 0 or self.ignoreIndividualTests:
            #no individual test counts available
            if allBuildsGood:
                if depFailed:
                    return '<span class="text-muted">%s</span>' % octicon("x")

                if suitesNotRun:
                    if activeCount:
                        return ""

                    if tests[0].calculatedPriority == 0:
                        return '<span class="text-muted">%s</span>' % "..."
                    return octicon("watch")
                    
                return octicon("check")
            else:
                return build_summary
        else:
            ratio_text = self.renderFailureCount(totalFailedTestCount, totalTests)

            if allBuildsGood:
                if not depFailed and not suitesNotRun:
                    return ratio_text
                if depFailed:
                    return ratio_text + '&nbsp;<span class="text-danger">(%s)</span>' % octicon("alert")
                if suitesNotRun:
                    if activeCount:
                        return ratio_text
                    return ratio_text + '&nbsp;<span class="text-muted">(...)</span>'
            else:
                if badBuilds:
                    return ratio_text + '&nbsp;<span class="text-danger">(%s)</span>' % octicon("alert")
                else:
                    if activeCount:
                        return ratio_text
                    return ratio_text + '&nbsp;<span class="text-muted">(...)</span>'


    @staticmethod
    def renderFailureCount(totalFailedTestCount, totalTests, verbose=False):
        if not verbose:
            if totalTests == 0:
                return '<span class="text-muted">%s</span>' % octicon("check")

        if verbose:
            return '<span class="text-danger">%d</span>%s%d' % (totalFailedTestCount, '<span class="text-muted px-1"> failed out of </span>', totalTests)
        else:
            return '<span class="text-danger">%d</span>%s%d' % (totalFailedTestCount, '<span class="text-muted px-1">/</span>', totalTests)
