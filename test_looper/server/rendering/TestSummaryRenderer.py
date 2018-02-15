import test_looper.server.HtmlGeneration as HtmlGeneration

octicon = HtmlGeneration.octicon

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
    def __init__(self, tests):
        self.tests = tests

    @cached
    def allBuilds(self):
        return [t for t in self.tests if t.testDefinition.matches.Build]

    @cached
    def allTests(self):
        return [t for t in self.tests if t.testDefinition.matches.Test]

    @cached
    def allEnvironments(self):
        envs = set()
        for t in self.tests:
            envs.add(t.testDefinition.environment)
        return envs

    @cached
    def hasOneEnvironment(self):
        return len(self.allEnvironments()) == 1

    def renderSummary(self):
        #first, see whether we have any tests
        if not self.tests or not self.allEnvironments():
            return ""

        button_text = self.renderSingleEnvironment()

        active = sum(t.activeRuns for t in self.tests)
        if active:
            button_text = '<span class="pr-1">%s</span>' % button_text
            button_text += '<span class="badge badge-info pl-1" title="{workers} jobs running">{workers}{icon}</span>'.format(workers=max(active,0), icon=octicon("pulse"))

        return button_text

    def renderMultipleEnvironments(self):
        return "%s builds over %s environments" % (len(self.allBuilds()), len(self.allEnvironments()))

    def categorizeBuild(self, b):
        if b.successes > 0:
            return "OK"
        if b.priority.matches.WaitingToRetry:
            return "PENDING"
        if b.priority.matches.DependencyFailed or b.totalRuns > 0:
            return "BAD"
        return "PENDING"

    def renderSingleEnvironment(self):
        #first, see if all of our builds have completed
        goodBuilds = 0
        badBuilds = 0
        waitingBuilds = 0

        builds = self.allBuilds()
        for b in builds:
            category = self.categorizeBuild(b)
            if category == "OK":
                goodBuilds += 1
            if category == "BAD":
                badBuilds += 1
            if category == "PENDING":
                waitingBuilds += 1

        if badBuilds:
            if badBuilds == len(builds):
                return """<span class="text-danger">%s</span>""" % octicon("x")

        if waitingBuilds:
            if builds[0].commitData.commit.userPriority == 0:
                return '<span class="text-muted">%s</span>' % "..."
            return "..."

        tests = self.allTests()

        if not tests:
            return octicon("check")

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

        if depFailed:
            return '<span class="text-muted">%s</span>' % octicon("x")

        if suitesNotRun:
            if tests[0].commitData.commit.userPriority == 0:
                return '<span class="text-muted">%s</span>' % "..."
            return "..."
            
        if totalTests == 0:
            return '<span class="text-muted">%s</span>' % octicon("check")

        if totalFailedTestCount == 0:
            return '%d%s' % (testTypes, '<span class="text-success">%s</span>' % octicon("check"))
        return '<span class="text-danger">%d</span>%s%d' % (totalFailedTestCount, '<span class="text-muted px-1">/</span>', totalTests)

