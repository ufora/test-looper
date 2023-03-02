import test_looper.server.HtmlGeneration as HtmlGeneration
import cgi

octicon = HtmlGeneration.octicon


def formatFloatToStringWithRoundoff(x):
    if abs(x - round(x, 0)) < 0.01:
        return str(int(round(x, 0)))
    return "%.1f" % x


def cached(f):
    def function(self):
        cname = "_cache" + f.__name__
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

    def renderSummary(self, label="", extraStyle=""):
        # first, see whether we have any tests
        activeTests = sum(t.activeRuns for t in self.allTests())
        activeBuilds = sum(t.activeRuns for t in self.allBuilds())
        active = activeTests + activeBuilds

        if not self.tests:
            button_text = '<span class="text-muted" style="width:30px">&nbsp;</span>'
        else:
            button_text = self.renderButtonContents(active)

        if activeTests:
            button_text = (
                button_text
                + ("&nbsp;" if button_text else "")
                + (
                    '<span class="badge badge-info pl-1">{workers}{icon}</span>'.format(
                        workers=max(activeTests, 0), icon=octicon("pulse")
                    )
                )
            )
        if activeBuilds:
            button_text = (
                button_text
                + ("&nbsp;" if button_text else "")
                + (
                    '<span class="badge badge-info pl-1">{workers}{icon}</span>'.format(
                        workers=max(activeBuilds, 0), icon=octicon("tools")
                    )
                )
            )

        testLooksBrokenTotal = sum(
            [t.testResultSummary.testLooksBrokenTotal for t in self.tests]
        )
        testLooksFixedTotal = sum(
            [t.testResultSummary.testLooksBrokenTotal for t in self.tests]
        )

        if False:  # lets not show this until we see taht the numbers make more sense
            if testLooksBrokenTotal:
                button_text = (
                    button_text
                    + ("&nbsp;" if button_text else "")
                    + (
                        '<span class="badge badge-info pl-1">{broken}{icon}</span>'.format(
                            broken=testLooksBrokenTotal, icon=octicon("bug")
                        )
                    )
                )
            if testLooksFixedTotal:
                button_text = (
                    button_text
                    + ("&nbsp;" if button_text else "")
                    + (
                        '<span class="badge badge-info pl-1">{fixed}{icon}</span>'.format(
                            fixed=testLooksFixedTotal, icon=octicon("thumbsup")
                        )
                    )
                )

        if label:
            button_text = label + "&nbsp;" + button_text

        summary = self.tooltipSummary()

        if summary:
            summary = "<span>%s</span>" % summary

        if active:
            if summary:
                summary += "&nbsp;"

            summary += "<span>%s active jobs</span>" % active

        if summary:
            if self.url:
                button_text = '<div onclick="location.href=\'{url}\';" class="clickable-div {extraStyle}" data-toggle="tooltip" title="{summary}" data-html="true">{text}</div>'.format(
                    summary=cgi.escape(summary),
                    text=button_text,
                    url=self.url,
                    extraStyle=extraStyle,
                )
            else:
                button_text = '<span data-toggle="tooltip" title="{summary}" data-html="true" class="{extraStyle}">{text}</span>'.format(
                    summary=cgi.escape(summary), text=button_text, extraStyle=extraStyle
                )

        elif self.url:
            button_text = '<div onclick="location.href=\'{url}\';" class="clickable-div {extraStyle}" title="{summary}" data-html="true">{text}</div>'.format(
                summary=cgi.escape(summary),
                text=button_text,
                url=self.url,
                extraStyle=extraStyle,
            )

        return button_text

    def categorizeAllBuilds(self):
        goodBuilds = []
        badBuilds = []
        waitingBuilds = []
        unprioritizedBuilds = []
        runningBuilds = []

        builds = self.allBuilds()
        for b in builds:
            if b.successes > 0:
                goodBuilds.append(b)
            elif b.totalRuns == 0 and b.activeRuns == 0 and b.calculatedPriority == 0:
                unprioritizedBuilds.append(b)
            elif b.activeRuns == 0 and b.priority.matches.WaitingToRetry:
                waitingBuilds.append(b)
            elif b.priority.matches.DependencyFailed or b.totalRuns > 0:
                badBuilds.append(b)
            elif b.activeRuns:
                runningBuilds.append(b)
            else:
                waitingBuilds.append(b)

        return goodBuilds, badBuilds, waitingBuilds, runningBuilds, unprioritizedBuilds

    def tooltipSummary(self):
        # first, see if all of our builds have completed
        goodBuilds, badBuilds, waitingBuilds, runningBuilds, unprioritizedBuilds = (
            self.categorizeAllBuilds()
        )

        res = ""
        if badBuilds:
            res += "<div>%s builds failed</div>" % (len(badBuilds))
        if goodBuilds:
            res += "<div>%s builds succeeded</div>" % (len(goodBuilds))
        if runningBuilds:
            res += "<div>%s builds running</div>" % (len(runningBuilds))
        if waitingBuilds:
            res += "<div>%s builds waiting</div>" % (len(waitingBuilds))
        if unprioritizedBuilds:
            res += "<div>%s builds defined but not prioritized</div>" % (
                len(unprioritizedBuilds)
            )

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
                    elif t.runsDesired:
                        suitesNotRun += 1
            elif t.successes == 0:
                suitesFailed += 1
            else:
                if t.testResultSummary.totalTestCount == 0:
                    suitesWithNoIndividualTests += 1
                else:
                    suitesSucceeded += 1

            totalTests += t.testResultSummary.totalTestCount
            totalFailedTestCount += t.testResultSummary.avgFailureRate

        if suitesWithNoIndividualTests:
            res += (
                "<div>%s test suites succeeded but dumped no individual tests</div>"
                % suitesWithNoIndividualTests
            )

        if suitesDepFailed:
            res += "<div>%s test suites had failed dependencies</div>" % suitesDepFailed

        if suitesRunning:
            res += "<div>%s test suites are actively running</div>" % suitesRunning

        if suitesNotRun:
            res += "<div>%s test suites are waiting to run</div>" % suitesNotRun

        if suitesNotRunAndNotPrioritized:
            res += (
                "<div>%s test suites are waiting to run but are not prioritized</div>"
                % suitesNotRunAndNotPrioritized
            )

        if suitesSucceeded:
            res += "<div>%s test suites ran</div>" % suitesSucceeded

        if suitesFailed:
            res += "<div>%s test suites failed</div>" % suitesFailed

        totalTests = formatFloatToStringWithRoundoff(totalTests)
        totalFailedTestCount = formatFloatToStringWithRoundoff(totalFailedTestCount)

        if totalTests:
            res += "<div>%s / %s individual test runs failed.</div>" % (
                totalFailedTestCount,
                totalTests,
            )

            testLooksGoodTotal = sum(
                [t.testResultSummary.testLooksGoodTotal for t in self.tests]
            )
            testLooksBadTotal = sum(
                [t.testResultSummary.testLooksBadTotal for t in self.tests]
            )
            testLooksFlakeyTotal = sum(
                [t.testResultSummary.testLooksFlakeyTotal for t in self.tests]
            )
            testLooksBrokenTotal = sum(
                [t.testResultSummary.testLooksBrokenTotal for t in self.tests]
            )
            testLooksFixedTotal = sum(
                [t.testResultSummary.testLooksFixedTotal for t in self.tests]
            )
            testLooksNewTotal = sum(
                [t.testResultSummary.testLooksNewTotal for t in self.tests]
            )

            if testLooksFlakeyTotal:
                res += (
                    "<div>%s individual tests look flakey.</div>" % testLooksFixedTotal
                )
            if testLooksNewTotal:
                res += "<div>%s individual tests are new.</div>" % testLooksNewTotal
            if testLooksBrokenTotal:
                res += (
                    "<div>%s individual tests are broken in this commit.</div>"
                    % testLooksBrokenTotal
                )
            if testLooksFixedTotal:
                res += (
                    "<div>%s individual tests are fixed in this commit.</div>"
                    % testLooksFixedTotal
                )

        return res

    def renderButtonContents(self, activeCount):
        # first, see if all of our builds have completed
        goodBuilds, badBuilds, waitingBuilds, runningBuilds, unprioritizedBuilds = (
            self.categorizeAllBuilds()
        )
        tests = self.allTests()

        totalTests = 0
        totalFailedTestCount = 0

        suitesNotRun = 0
        suitesFailed = 0
        depFailed = 0
        suitesSucceeded = 0
        suitesNotRunAndNotPrioritized = 0

        for t in tests:
            if t.totalRuns == 0 and t.priority.matches.DependencyFailed:
                depFailed += 1
            elif t.totalRuns == 0:
                if t.calculatedPriority:
                    suitesNotRun += 1
                else:
                    suitesNotRunAndNotPrioritized += 1
            elif t.successes == 0:
                suitesFailed += 1
            else:
                suitesSucceeded += 1

            totalTests += t.testResultSummary.totalTestCount
            totalFailedTestCount += t.testResultSummary.avgFailureRate

        build_summary = ""
        allBuildsGood = False

        if badBuilds:
            build_summary = """<span class="text-danger">%s</span>""" % octicon("x")
        elif waitingBuilds or runningBuilds:
            if activeCount:
                return ""
            build_summary = octicon("watch")
        else:
            if goodBuilds:
                build_summary = octicon("check")
            elif unprioritizedBuilds:
                build_summary = '<span class="text-muted">...</span>'
            allBuildsGood = True

        if not tests:
            # we have no tests, but the builds passed
            return build_summary

        if totalTests == 0 or self.ignoreIndividualTests:
            # no individual test counts available
            if allBuildsGood:
                if depFailed or suitesFailed:
                    if not suitesSucceeded:
                        return '<span class="text-muted">%s</span>' % octicon("x")
                    else:
                        return '<span class="text-muted">%s</span>' % octicon("alert")

                if suitesNotRun:
                    if activeCount:
                        return ""

                    if tests[0].calculatedPriority == 0:
                        return '<span class="text-muted">%s</span>' % "..."
                    return octicon("watch")

                if suitesSucceeded:
                    return octicon("check")

            return build_summary
        else:
            ratio_text = self.renderFailureCount(totalFailedTestCount, totalTests)

            if allBuildsGood:
                if depFailed:
                    return (
                        ratio_text
                        + '&nbsp;<span class="text-danger">(%s)</span>'
                        % octicon("alert")
                    )
                if not depFailed and not suitesNotRun:
                    return ratio_text
                if suitesNotRun:
                    if activeCount:
                        return ratio_text
                    return ratio_text + '&nbsp;<span class="text-muted">(...)</span>'
            else:
                if badBuilds:
                    return (
                        ratio_text
                        + '&nbsp;<span class="text-danger">(%s)</span>'
                        % octicon("alert")
                    )
                else:
                    if activeCount:
                        return ratio_text
                    return ratio_text + '&nbsp;<span class="text-muted">(...)</span>'

    @staticmethod
    def renderFailureCount(totalFailedTestCount, totalTests, verbose=False):
        totalFailedTestCount = formatFloatToStringWithRoundoff(totalFailedTestCount)
        totalTests = formatFloatToStringWithRoundoff(totalTests)

        if not verbose:
            if totalTests == 0:
                return '<span class="text-muted">%s</span>' % octicon("check")

        if verbose:
            return '<span class="text-danger">%s</span>%s%s' % (
                totalFailedTestCount,
                '<span class="text-muted px-1"> failed out of </span>',
                totalTests,
            )
        else:
            return '<span class="text-danger">%s</span>%s%s' % (
                totalFailedTestCount,
                '<span class="text-muted px-1">/</span>',
                totalTests,
            )
