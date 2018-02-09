import cherrypy
import dateutil.parser
import itertools
import math
import os
import sys
import yaml
import time
import logging
import tempfile
import threading
import markdown
import urllib
import urlparse
import pytz
import simplejson
import struct
import os
import test_looper.core.DirectoryScope as DirectoryScope
import test_looper.core.SubprocessRunner as SubprocessRunner
import test_looper.core.source_control as Github
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.core.algebraic_to_json as algebraic_to_json

HtmlGeneration = reload(HtmlGeneration)

def secondsUpToString(up_for):
    if up_for < 60:
        return ("%d seconds" % up_for)
    elif up_for < 60 * 60 * 2:
        return ("%.1f minutes" % (up_for / 60))
    elif up_for < 24 * 60 * 60 * 2:
        return ("%.1f hours" % (up_for / 60 / 60))
    else:
        return ("%.1f days" % (up_for / 60 / 60 / 24))

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
    def __init__(self, main_renderer, tests):
        self.main_renderer = main_renderer
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
            if t.fullyResolvedEnvironment.matches.Resolved:
                envs.add(t.fullyResolvedEnvironment.Environment)
        return envs

    @cached
    def hasOneEnvironment(self):
        return len(self.allEnvironments()) == 1

    def renderSummary(self):
        #first, see whether we have any tests
        if not self.tests or not self.allEnvironments():
            return ""

        if len(self.allEnvironments()) > 1:
            return self.renderMultipleEnvironments()
        else:
            return self.renderSingleEnvironment()

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
        env = list(self.allEnvironments())[0]
        
        active = sum(t.activeRuns for t in self.tests)
        if active:
            return "%s running" % active

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
                return """
                    <span class="alert-danger"><span class="octicon octicon-issue-opened" aria-hidden="true" ></span></span>
                    """

        if waitingBuilds:
            if builds[0].commitData.commit.userPriority == 0:
                return HtmlGeneration.lightGrey("%s builds" % len(builds)).render()
            return HtmlGeneration.lightGrey("builds pending").render()

        tests = self.allTests()

        if not tests:
            return '<span class="octicon octicon-check" aria-hidden="true"></span>'

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
            return '<span class="text-muted"><span class="octicon octicon-issue-opened" aria-hidden="true"></span></span>'

        if suitesNotRun:
            if tests[0].commitData.commit.userPriority == 0:
                return HtmlGeneration.lightGrey("%s suites" % len(tests)).render()
            return HtmlGeneration.lightGrey("%s suites pending" % suitesNotRun).render()
            
        return "%d/%d tests failing" % (totalFailedTestCount, totalTests)


class TestGridRenderer:
    """Describes a mechanism for grouping tests into columns and rows along with state to filter and expand."""
    def __init__(self, tests_in_rows, row_ordering, column_expansions = None):
        if column_expansions is None:
            #by default, expand the first - of the environment
            column_expansions = {(): {"type":"env", "prefix": 0}}

        self.row_ordering = row_ordering
        self.columnExpansions = column_expansions
        self.column_children = {}
        self.leafColumnAllTests = {}
        self.tests_in_rows_and_columns = {k: self.breakTestsIntoColumns(v) for k,v in tests_in_rows.iteritems()}
        self.column_widths = {}

        self.computeColumnWidths()

    def computeColumnWidths(self):
        def compute(col):
            if col not in self.column_children:
                return 1
            res = 0
            for child in self.column_children[col]:
                res += compute(col + (child,))

            self.column_widths[col] = res

            return res

        compute(())

    def breakTestsIntoColumns(self, tests):
        row = {}

        for t in tests:
            col = self.testGetColumn(t)
            if col not in row:
                row[col] = []
            row[col].append(t)

            if col not in self.leafColumnAllTests:
                self.leafColumnAllTests[col] = []
            self.leafColumnAllTests[col].append(t)

        return row

    def testGetColumn(self, t):
        curColumn = ()

        while curColumn in self.columnExpansions:
            expansion = self.columnExpansions[curColumn]
            group = self.applyExpansion(t, expansion)

            if curColumn not in self.column_children:
                self.column_children[curColumn] = set()
            self.column_children[curColumn].add(group)

            curColumn = curColumn + (group,)

        return curColumn

    def envNameForTest(self, test):
        if test.fullyResolvedEnvironment.matches.Resolved:
            return test.fullyResolvedEnvironment.Environment.environment_name.split("/")[-1]
        else:
            return "Unresolved"

    def applyExpansion(self, test, expansion):
        if expansion["type"] == "env":
            name = self.envNameForTest(test)

            name = name.split("-")
            if expansion["prefix"] > len(name):
                return None
            else:
                return name[expansion["prefix"]]
        return None

    def columnsInOrder(self):
        columns = []
        def walk(col):
            if col not in self.column_children:
                columns.append(col)
            else:
                for child in sorted(self.column_children[col]):
                    walk(col + (child,))
        walk(())

        return columns

    def getGridHeaders(self, url_fun):
        header_meaning = [[()]]

        while [h for h in header_meaning[-1] if h is not None]:
            new_header_meaning = []
            for h in header_meaning[-1]:
                if h is None:
                    new_header_meaning.append(None)
                elif h in self.column_children:
                    for child in self.column_children[h]:
                        new_header_meaning.append(h + (child,))
                else:
                    new_header_meaning.append(None)

            header_meaning.append(new_header_meaning)

        def cellForHeader(group):
            if group is None:
                return ""
            if group not in self.column_children:
                return self.groupHeader(group, url_fun)

            return {"content": self.groupHeader(group, url_fun), "colspan": self.column_widths[group]}

        return [[cellForHeader(c) for c in line] for line in header_meaning[1:-1]]

    def groupHeader(self, group, url_fun):
        if group not in self.leafColumnAllTests:
            canExpand = False
            canCollapse = True
        else:
            canCollapse = False
            canExpand = len(set(self.envNameForTest(t) for t in self.leafColumnAllTests[group])) > 1

        name = group[-1] if group else ""

        if not group:
            canCollapse = False

        if canExpand:
            expansions = dict(self.columnExpansions)
            expansions[group] = {"type": "env", "prefix": len(group)}
            name = HtmlGeneration.link(name, url_fun(expansions)).render()

        if canCollapse:
            expansions = dict(self.columnExpansions)
            del expansions[group]
            name = HtmlGeneration.link(name, url_fun(expansions)).render()

        return name

    def render_row(self, row_identifier, url_fun):
        return [
            TestSummaryRenderer(
                self, 
                self.tests_in_rows_and_columns[row_identifier].get(c, [])
                ).renderSummary()
                for c in self.columnsInOrder()
            ]

def HtmlWrapper(f):
    def inner(self, *args, **kwargs):
        return HtmlGeneration.headers + self.commonHeader() + f(self, *args, **kwargs) + HtmlGeneration.footers
    return inner

class Renderer:
    def __init__(self, httpServer):
        self.httpServer = httpServer
        self.testManager = httpServer.testManager
        self.eventLog = httpServer.eventLog
        self.artifactStorage = httpServer.artifactStorage
        self.address = httpServer.address
        self.src_ctrl = httpServer.src_ctrl

    def can_write(self):
        return self.httpServer.can_write()

    def is_authenticated(self):
        return self.httpServer.is_authenticated()

    def getCurrentLogin(self):
        return self.httpServer.getCurrentLogin()

    @HtmlWrapper
    def errorPage(self, errorMessage):
        return markdown.markdown("#ERROR\n\n" + errorMessage)

    @HtmlWrapper
    def test(self, testId):
        with self.testManager.database.view():
            testRun = self.testManager.getTestRunById(testId)

            if testRun is None:
                return self.errorPage("Unknown testid %s" % testId)

            grid = [["ARTIFACTS"]]

            commit = testRun.test.commitData.commit

            if testRun.test.testDefinition.matches.Build:
                build_key = testRun.test.testDefinition.name.replace("/","_") + ".tar.gz"

                if self.artifactStorage.build_exists(commit.repo.name, commit.hash, build_key):
                    grid.append([
                        HtmlGeneration.link(build_key, self.buildDownloadUrl(commit.repo.name, commit.hash, build_key))
                        ])
                else:
                    logging.info("No build found at %s", build_key)

            for artifactName in self.artifactStorage.testResultKeysFor(commit.repo.name, commit.hash, testId):
                grid.append([
                    HtmlGeneration.link(
                        artifactName,
                        self.testResultDownloadUrl(testId, artifactName)
                        )
                    ])

            if testRun.totalTestCount:
                individual_tests_grid = [["TEST_NAME", "PASSED"]]
                pass_dict = {}

                for ix in xrange(len(testRun.testNames.test_names)):
                    pass_dict[testRun.testNames.test_names[ix]] = "PASS" if testRun.testFailures[ix] else "FAIL"

                for k,v in sorted(pass_dict.items()):
                    individual_tests_grid.append((k,v))

                individual_tests = markdown.markdown("## Tests") + HtmlGeneration.grid(individual_tests_grid)
            else:
                individual_tests = ""

            return (
                markdown.markdown("# Test\n") +
                markdown.markdown("Test: %s\n" % testId) +
                markdown.markdown("## Commit ") + 
                self.commitLink(testRun.test.commitData.commit).render() + 
                markdown.markdown("##") + 
                self.testLogsButton(testId).render() + 
                markdown.markdown("## Artifacts\n") +
                (HtmlGeneration.grid(grid) if grid else "") + 
                individual_tests
                )

    def test_contents(self, testId, key):
        with self.testManager.database.view():
            testRun = self.testManager.getTestRunById(testId)

            assert testRun

            commit = testRun.test.commitData.commit

            return self.processFileContents(
                self.artifactStorage.testContentsHtml(commit.repo.name, commit.hash, testId, key)
                )

    def processFileContents(self, contents):
        if contents.matches.Redirect:
            logging.info("Redirecting to %s", contents.url)
            raise cherrypy.HTTPRedirect(contents.url)

        if contents.content_type:
            cherrypy.response.headers['Content-Type'] = contents.content_type
        if contents.content_disposition:
            cherrypy.response.headers["Content-Disposition"] = contents.content_disposition
        if contents.content_encoding:
            cherrypy.response.headers["Content-Encoding"] = contents.content_encoding

        return contents.content

    def deleteTestRunButton(self, testId):
        return HtmlGeneration.Link(
            self.deleteTestRunUrl(testId),
            "CLEAR", 
            is_button=True,
            button_style=self.disable_if_cant_write('btn-primary btn-xs')
            )

    def testLogsButton(self, testId):
        return HtmlGeneration.Link(
            self.testLogsUrl(testId),
            "LOGS", 
            is_button=True,
            button_style=self.disable_if_cant_write('btn-primary btn-xs')
            )

    def clearTestRun(self, testId, redirect):
        self.testManager.clearTestRun(testId)

        with self.testManager.database.view():
            testRun = self.testManager.getTestRunById(testId)

            if testRun.test.testDefinition.matches.Build:
                commit = testRun.test.commitData.commit

                build_key = testRun.test.testDefinition.name.replace("/","_") + ".tar.gz"

                self.artifactStorage.clear_build(commit.repo.name, commit.hash, build_key)

        raise cherrypy.HTTPRedirect(redirect)

    def deleteTestRunUrl(self, testId):
        return self.address + "/clearTestRun?" + urllib.urlencode({"testId": testId, "redirect": self.redirect()})

    def testLogsUrl(self, testId):
        return self.address + "/testLogs?testId=%s" % testId

    def testLogs(self, testId):
        with self.testManager.database.view():
            testRun = self.testManager.getTestRunById(testId)
            if testRun.endTimestamp < 1.0:
                raise cherrypy.HTTPRedirect(self.testLogsLiveUrl(testId))
            else:
                raise cherrypy.HTTPRedirect(self.testResultDownloadUrl(testId, "test_looper_log.txt"))
   
    def testLogsLiveUrl(self, testId):
        return self.address + "/terminalForTest?testId=%s" % testId
   
    def testResultDownloadUrl(self, testId, key):
        return self.address + "/test_contents?" + urllib.urlencode({"testId": testId, "key": key})

    def build_contents(self, repoName, commitHash, key):
        return self.processFileContents(self.artifactStorage.buildContentsHtml(repoName, commitHash, key))

    def buildDownloadUrl(self, repoName, commitHash, key):
        return self.address + "/build_contents?" + urllib.urlencode({"key": key, "repoName": repoName, "commitHash": commitHash})

    def allTestsLink(self, text, commit, testName, failuresOnly=False):
        extras = {}

        if failuresOnly:
            extras["failuresOnly"] = 'true'
        if testName:
            extras["testName"] = testName

        extras["repoName"] = commit.repo.name
        extras["commitHash"] = commit.hash

        return HtmlGeneration.link(
            text,
            self.address + "/allTestRuns" + ("?" if extras else "") + urllib.urlencode(extras)
            )

    def wellNamedCommitLinkAsStr(self, commit, branch, branchExtension):
        if not branchExtension:
            branchExtension = "HEAD"

        return (
            self.branchesLink(branch.repo.name).render() + "/" + 
            self.branchLink(branch).render() + "/" + 
            self.commitLink(commit, textOverride=branchExtension + "&nbsp;" * max(0, 5 - len(branchExtension))).render()
            )

    def commitLink(self, commit, textOverride = None, textIsSubject=True, hoverOverride=None):
        if textOverride is not None:
            text = textOverride
        elif textIsSubject:
            subject = "<not loaded yet>" if not commit.data else commit.data.subject
            text = subject if len(subject) < 71 else subject[:70] + '...'
        else:
            text = commit.repo.name + "/" + commit.hash[:8]

        extras = {}

        extras["repoName"] = commit.repo.name
        extras["commitHash"] = commit.hash

        return HtmlGeneration.link(
            text,
            self.address + "/commit" + ("?" if extras else "") + urllib.urlencode(extras),
            hover_text=hoverOverride or ("commit " + commit.hash[:10] + " : " + ("" if not commit.data else commit.data.commitMessage))
            )

    def branchLink(self, branch, testGroupsToExpand=None):
        return HtmlGeneration.link(branch.branchname, self.branchUrl(branch, testGroupsToExpand))

    def branchUrl(self, branch, testGroupsToExpand=None):
        args = {"reponame": branch.repo.name, "branchname": branch.branchname}
        if testGroupsToExpand:
            args["testGroupsToExpand"] = ",".join(testGroupsToExpand)
        return self.address + "/branch?" + urllib.urlencode(args)


    def disable_if_cant_write(self, style):
        if self.can_write() or "disabled" in style:
            return style
        return style + " disabled"


    def small_clear_button(self, url, label=None):
        label = label or "clear"
        return HtmlGeneration.Link(url,
                                   label,
                                   is_button=True,
                                   button_style=self.disable_if_cant_write('btn-primary btn-xs'))


    def clearBranchLink(self, branch):
        return self.small_clear_button(
            "/clearBranch?" + urllib.urlencode({'reponame': branch.repo.name, 'branchname':branch.branchname, 'redirect': self.redirect()}),
            )

    def clearCommitIdLink(self, commitId):
        return self.small_clear_button(
            "/clearCommit?" + urllib.urlencode({'commitId': commitId, 'redirect': self.redirect()}),
            )

    def clearTestLink(self, testname):
        return self.small_clear_button(
            "/clearTest?" + urllib.urlencode({'testname': testname, 'redirect': self.redirect()}),
            )

    def sourceLinkForCommit(self, commit):
        url = self.src_ctrl.commit_url(commit.repo.name, commit.hash)
        if url:
            return HtmlGeneration.link(commit.hash[:7], url)
        else:
            return HtmlGeneration.lightGrey(commit.hash[:7])

    def clearCommit(self, commitId, redirect):
        with self.testManager.database.view():
            self.testManager.clearCommitId(commitId)

        raise cherrypy.HTTPRedirect(redirect)

    def clearBranch(self, branch, redirect=None):
        with self.testManager.database.view():
            commits = self.testManager.branches[branch].commits

            for c in commits:
                self.testManager.clearCommitId(c)

        if redirect is not None:
            raise cherrypy.HTTPRedirect(redirect)

    def cancelTestRun(self, testRunId, redirect):
        with self.testManager.transaction_and_lock():
            testRun = self.testManager.getTestRunById(testRunId)

            if testRun is None:
                return self.errorPage("Unknown testid %s" % testRunId)

            if not testRun.canceled:
                self.testManager._cancelTestRun(testRun, time.time())

        raise cherrypy.HTTPRedirect(redirect)

    def cancelTestRunButton(self, testRunId):
        return HtmlGeneration.Link(
            self.address + "/cancelTestRun?" + urllib.urlencode({"testRunId":testRunId, "redirect": self.redirect()}),
            "cancel", 
            is_button=True,
            button_style=self.disable_if_cant_write('btn-primary btn-xs')
            )        
    
    @HtmlWrapper
    def machines(self):
        with self.testManager.database.view():
            machines = self.testManager.database.Machine.lookupAll(isAlive=True)

            grid = [["MachineID", "Hardware", "OS", "BOOTED AT", "UP FOR", "STATUS", "LASTMSG", "COMMIT", "TEST", "LOGS", "CANCEL", ""]]
            for m in sorted(machines, key=lambda m: -m.bootTime):
                row = []
                row.append(m.machineId)
                row.append("%s cores, %s GB" % (m.hardware.cores, m.hardware.ram_gb))
                if m.os.matches.WindowsVM:
                    row.append("Win(%s)" % m.os.ami)
                elif m.os.matches.LinuxVM:
                    row.append("Linux(%s)" % m.os.ami)
                elif m.os.matches.LinuxWithDocker:
                    row.append("LinuxDocker()")
                elif m.os.matches.WindowsWithDocker:
                    row.append("WindowsDocker()")
                else:
                    row.append("Unknown")

                row.append(time.asctime(time.gmtime(m.bootTime)))
                row.append(secondsUpToString(time.time() - m.bootTime))
                
                if m.firstHeartbeat < 1.0:
                    row.append("BOOTING")
                else:
                    row.append("Heartbeat %s seconds ago" % int(time.time() - m.lastHeartbeat))
                
                row.append(m.lastHeartbeatMsg)

                tests = self.testManager.database.TestRun.lookupAll(runningOnMachine=m)
                deployments = self.testManager.database.Deployment.lookupAll(runningOnMachine=m)

                if len(tests) + len(deployments) > 1:
                    row.append("ERROR: multiple test runs/deployments")
                elif tests:
                    commit = tests[0].test.commitData.commit
                    try:
                        row.append(self.commitLink(commit, textOverride=commit.repo.name + "/" + self.testManager.bestCommitName(commit)))
                    except:
                        row.append("")

                    row.append(self.testRunLink(tests[0], "TEST "))
                    row.append(self.testLogsButton(tests[0]._identity))
                    row.append(self.cancelTestRunButton(tests[0]._identity))
                    
                elif deployments:
                    commit = deployments[0].test.commitData.commit
                    try:
                        row.append(self.commitLink(commit, textOverride=commit.repo.name + "/" + self.testManager.bestCommitName(commit)))
                    except:
                        row.append("")

                    d = deployments[0]
                    row.append("DEPLOYMENT")
                    row.append(self.connectDeploymentLink(d))
                    row.append(self.shutdownDeploymentLink(d))
                
                grid.append(row)
                
            return HtmlGeneration.grid(grid)

    @HtmlWrapper
    def commit(self, repoName, commitHash):
        with self.testManager.database.view():
            repo = self.testManager.database.Repo.lookupAny(name=repoName)
            if not repo:
                return self.errorPage("Repo %s doesn't exist" % repoName)

            commit = self.testManager.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))
            if not commit:
                return self.errorPage("Commit %s/%s doesn't exist" % (repo, commitHash))

            if not commit.data:
                return self.errorPage("Commit hasn't been imported yet")

            tests = self.testManager.database.Test.lookupAll(commitData=commit.data)
            
            tests = sorted(tests, key=lambda test: test.fullname)
            

            grid = [["TEST", "", "", "ENVIRONMENT", "RUNNING", "COMPLETED", "FAILED", "PRIORITY", "AVG_TEST_CT", "AVG_FAILURE_CT", "AVG_RUNTIME", "", "TEST_DEPS"]]

            for t in tests:
                row = []

                row.append(
                    self.allTestsLink(t.testDefinition.name, commit, t.testDefinition.name)
                    )
                row.append("") #self.clearTestLink(t.fullname))
                row.append(
                    HtmlGeneration.Link(self.bootTestOrEnvUrl(t.fullname),
                       "BOOT",
                       is_button=True,
                       new_tab=True,
                       button_style=self.disable_if_cant_write('btn-primary btn-xs')
                       )
                    )

                row.append(self.environmentLink(t, t.testDefinition.environment_name))

                row.append(str(t.activeRuns))
                row.append(str(t.totalRuns))
                row.append(str(t.totalRuns - t.successes))

                def stringifyPriority(calculatedPriority, priority):
                    if priority.matches.UnresolvedDependencies:
                        if t.fullyResolvedEnvironment.matches.Unresolved:
                            return "UnresolvedEnvironmentDependencies"
                        return "UnresolvedDependencies"
                    if priority.matches.HardwareComboUnbootable:
                        return "HardwareComboUnbootable"
                    if priority.matches.InvalidTestDefinition:
                        return "InvalidTestDefinition"
                    if priority.matches.WaitingOnBuilds:
                        return "WaitingOnBuilds"
                    if priority.matches.NoMoreTests:
                        return "HaveEnough"
                    if priority.matches.DependencyFailed:
                        return "DependencyFailed"
                    if (priority.matches.WantsMoreTests or priority.matches.FirstTest or priority.matches.FirstBuild):
                        return "WaitingForHardware"
                    if priority.matches.WaitingToRetry:
                        return "WaitingToRetry"

                    return "Unknown"

                row.append(stringifyPriority(t.calculatedPriority, t.priority))

                all_tests = list(self.testManager.database.TestRun.lookupAll(test=t))
                all_noncanceled_tests = [testRun for testRun in all_tests if not testRun.canceled]
                finished_tests = [testRun for testRun in all_noncanceled_tests if testRun.endTimestamp > 0.0]

                if t.totalRuns:
                    if t.totalRuns == 1:
                        #don't want to convert these to floats
                        row.append("%d" % t.totalTestCount)
                        row.append("%d" % t.totalFailedTestCount)
                    else:
                        row.append(str(t.totalTestCount / float(t.totalRuns)))
                        row.append(str(t.totalFailedTestCount / float(t.totalRuns)))

                    if finished_tests:
                        row.append(secondsUpToString(sum([testRun.endTimestamp - testRun.startedTimestamp for testRun in finished_tests]) / len(finished_tests)))
                    else:
                        row.append("")
                else:
                    row.append("")
                    row.append("")
                    
                    if all_noncanceled_tests:
                        row.append(secondsUpToString(sum([time.time() - testRun.startedTimestamp for testRun in all_noncanceled_tests]) / len(all_noncanceled_tests)) + " so far")
                    else:
                        row.append("")


                runButtons = []

                for testRun in all_noncanceled_tests:
                    runButtons.append(self.testLogsButton(testRun._identity).render())

                row.append(" ".join(runButtons))
                row.append(self.testDependencySummary(t))

                grid.append(row)

            header = self.commitPageHeader(commit)

            if commit.data.testDefinitionsError:
                raw_text, extension = self.testManager.getRawTestFileForCommit(commit)
                try:
                    if extension is None:
                        post_expansion_text = ""
                    else:
                        expansion = TestDefinitionScript.extract_postprocessed_test_definitions(extension, raw_text)
                        post_expansion_text = markdown.markdown("#### After macro expansion") + \
                            HtmlGeneration.PreformattedTag(yaml.dump(expansion)).render()
                except Exception as e:
                    post_expansion_text = markdown.markdown("#### Error parsing and expanding macros") + \
                        HtmlGeneration.PreformattedTag(traceback.format_exc()).render()

                return (
                    header + 
                    markdown.markdown("## Invalid Test Definitions\n\n#### ERROR") + 
                    HtmlGeneration.PreformattedTag(commit.data.testDefinitionsError).render() + 
                    markdown.markdown("#### Raw Test File") + 
                    HtmlGeneration.PreformattedTag(raw_text).render() + 
                    post_expansion_text
                    )
            else:
                return header + HtmlGeneration.grid(grid)

    def commitPageHeader(self, commit):
        repoName = commit.repo.name

        markdown_header = """## Repo [%s](%s)\n""" % (repoName, self.branchesUrl(repoName))
        markdown_header += """## Commit [`%s`](%s):\n%s\n""" % (
            commit.hash[:10], 
            self.commitLink(commit).url,
            "\n".join(["    " + x for x in commit.data.commitMessage.split("\n")])
            )

        markdown_header += """## Priority: toggle_switch """

        if commit.userPriority == 0:
            markdown_header += "Not Prioritized"
        else:
            markdown_header += "Prioritized"
        markdown_header = (
            markdown.markdown(markdown_header)
                .replace("toggle_switch", self.toggleCommitUnderTestLink(commit).render())
            )

        if not commit.anyBranch:
            branchgrid = [["COMMIT ORPHANED!"]]
        else:
            branchgrid = [["Branches Containing This Commit"]]

            for branch, path in self.testManager.commitFindAllBranches(commit).iteritems():
                branchgrid.append([self.branchLink(branch).render() + path])

        return markdown_header + HtmlGeneration.grid(branchgrid)


    def testDependencySummary(self, t):
        """Return a single cell displaying all the builds this test depends on"""
        failed = 0
        succeeded = 0
        running = 0
        sleeping = 0
        waiting_for_hardware = 0
        stuck = 0
        
        for depsOn in self.testManager.allTestsDependedOnByTest(t):
            if depsOn.successes:
                succeeded += 1
            elif depsOn.activeRuns:
                running += 1
            elif depsOn.totalRuns:
                if depsOn.priority.matches.WaitingToRetry:
                    sleeping += 1
                else:
                    failed += 1
            elif depsOn.priority.matches.FirstBuild:
                waiting_for_hardware += 1
            else:
                stuck += 1

        if not (failed+succeeded+running+sleeping+stuck+waiting_for_hardware):
            return ""

        res = []
        if waiting_for_hardware:
            res.append("%d booting" % waiting_for_hardware)
        if stuck:
            res.append("%d stuck" % stuck)
        if sleeping:
            res.append("%d sleeping" % sleeping)
        if failed:
            res.append("%d failed" % failed)
        if succeeded:
            res.append("%d succeeded" % succeeded)
        if running:
            res.append("%d running" % running)

        return ", ".join(res)

    @HtmlWrapper
    def allTestRuns(self, repoName, commitHash, failuresOnly, testName):
        with self.testManager.database.view():
            repo = self.testManager.database.Repo.lookupAny(name=repoName)
            if not repo:
                return self.errorPage("Repo %s doesn't exist" % repoName)

            commit = self.testManager.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))
            if not commit:
                return self.errorPage("Commit %s/%s doesn't exist" % (repo, commitHash))

            if not commit.data:
                return self.errorPage("Commit hasn't been imported yet")

            testTypes = self.testManager.database.Test.lookupAll(commitData=commit.data)

            if testName is not None:
                testTypes = [x for x in testTypes if x.testDefinition.name == testName]

            tests = []
            for test in testTypes:
                tests.extend(self.testManager.database.TestRun.lookupAll(test=test))
            
            tests = sorted(tests, key=lambda test: -test.startedTimestamp)
            
            if failuresOnly:
                tests = [x for x in tests if not x.success and x.endTimestamp > 0.0]

            grid = self.gridForTestList_(tests, commit=commit, failuresOnly=failuresOnly)

            header = self.commitPageHeader(commit)
            
            if failuresOnly:
                header += markdown.markdown("showing failures only. %s<br/><br/>" % \
                    self.allTestsLink("Show all test results", commit, testName).render())
            else:
                header += markdown.markdown("showing both successes and failures. %s<br/><br/>" % \
                    self.allTestsLink("Show only failures", commit, testName, failuresOnly=True).render())

            header += markdown.markdown("showing all test runs. [Show full commit summary](%s)<br/><br/>" % \
                    self.commitLink(commit).url)

            header = header

            if testName and len(testTypes) == 1:
                header += markdown.markdown("### Test Dependencies:\n") + HtmlGeneration.grid(self.allTestDependencyGrid(testTypes[0]))

            return header + HtmlGeneration.grid(grid)

    def allTestDependencyGrid(self, test):
        grid = [["COMMIT", "TEST"]]

        for subtest in self.testManager.allTestsDependedOnByTest(test):
            grid.append([
                self.commitLink(subtest.commitData.commit, textIsSubject=False),
                    self.allTestsLink(subtest.testDefinition.name, subtest.commitData.commit, subtest.testDefinition.name)
                ])

        for dep in self.testManager.database.UnresolvedTestDependency.lookupAll(test=test):
            grid.append(["Unresolved Test", dep.dependsOnName])
        for dep in self.testManager.database.UnresolvedSourceDependency.lookupAll(test=test):
            grid.append(["Unresolved Commit", dep.repo.name + "/" + dep.commitHash])
        for dep in self.testManager.database.UnresolvedRepoDependency.lookupAll(test=test):
            grid.append(["Unresolved Repo", dep.reponame + "/" + dep.commitHash])

        return grid

    def bootTestOrEnvUrl(self, fullname):
        return self.address + "/bootDeployment?" + urllib.urlencode({"fullname":fullname})

    def bootDeployment(self, fullname):
        try:
            deploymentId = self.testManager.createDeployment(fullname, time.time())
        except Exception as e:
            logging.error("Failed to boot a deployment:\n%s", traceback.format_exc())
            return self.errorPage("Couldn't boot a deployment for %s: %s" % (fullname, str(e)))

        logging.info("Redirecting for %s", fullname)
        
        raise cherrypy.HTTPRedirect(self.address + "/terminalForDeployment?deploymentId=" + deploymentId)

    def environmentLink(self, test, environmentName):
        return HtmlGeneration.link(environmentName, "/testEnvironment?" + urllib.urlencode(
            {"repoName": test.commitData.commit.repo.name,
             "commitHash": test.commitData.commit.hash,
             "environmentName": environmentName}
            ))

    @HtmlWrapper
    def testEnvironment(self, repoName, commitHash, environmentName):
        with self.testManager.database.view():
            repo = self.testManager.database.Repo.lookupAny(name=repoName)
            if not repo:
                return self.errorPage("Repo %s doesn't exist" % repoName)

            commit = self.testManager.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))
            if not commit or not commit.data:
                return self.errorPage("Commit %s/%s doesn't exist" % (repoName, commitHash))

            env = commit.data.environments.get(environmentName)
            if not env:
                return self.errorPage("Environment %s/%s/%s doesn't exist" % (repoName, commitHash, environmentName))

            text = algebraic_to_json.encode_and_dump_as_yaml(env)

            return HtmlGeneration.PreformattedTag(text).render()

    def testRunLink(self, testRun, text_prefix=""):
        return HtmlGeneration.link(text_prefix + str(testRun._identity)[:8], "/test?testId=" + testRun._identity)

    def gridForTestList_(self, sortedTests, commit=None, failuresOnly=False):
        grid = [["TEST", "TYPE", "STATUS", "LOGS", "CLEAR", "STARTED", "MACHINE", "ELAPSED (MIN)",
                 "SINCE LAST HEARTBEAT (SEC)", "TOTAL TESTS", "FAILING TESTS"]]

        sortedTests = [x for x in sortedTests if not x.canceled]
        
        for testRun in sortedTests:
            row = []

            row.append(self.testRunLink(testRun))

            name = testRun.test.testDefinition.name

            row.append(name)

            if testRun.endTimestamp > 0.0:
                row.append("passed" if testRun.success else "failed")
            else:
                row.append(self.cancelTestRunButton(testRun._identity))

            row.append(self.testLogsButton(testRun._identity))

            row.append(self.deleteTestRunButton(testRun._identity))

            row.append(time.ctime(testRun.startedTimestamp))

            if testRun.endTimestamp > 0.0:
                elapsed = (testRun.endTimestamp - testRun.startedTimestamp) / 60.0
            else:
                elapsed = (time.time() - testRun.startedTimestamp) / 60.0

            row.append(self.machineLink(testRun.machine))

            row.append("%.2f" % elapsed)

            if hasattr(testRun, "lastHeartbeat") and testRun.endTimestamp <= 0.0:
                timeSinceHB = time.time() - testRun.lastHeartbeat
            else:
                timeSinceHB = None

            row.append(str("%.2f" % timeSinceHB) if timeSinceHB is not None else "")

            if testRun.totalTestCount > 0:
                row.append(str(testRun.totalTestCount))
                row.append(str(testRun.totalFailedTestCount))
            else:
                row.append("")
                row.append("")

            grid.append(row)

        return grid

    @staticmethod
    def machineLink(machine):
        return HtmlGeneration.link(machine, "/machine?machineId="+machine.machineId)

    def login_link(self):
        self.save_current_url()
        return '<a href="%s">Login</a>' % self.src_ctrl.authenticationUrl()

    def logout_link(self):
        return ('<a href="/logout">'
                'Logout [<span class="octicon octicon-person" aria-hidden="true"/>%s]'
                '</a>') % self.getCurrentLogin()


    def commonHeader(self):
        headers = []

        nav_links = [
            ('Repos', '/repos'),
            ('Machines', '/machines'),
            ('Deployments', '/deployments')
            ]

        headers += ["""
            <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
            <div class="container-fluid">
            <button class="navbar-toggler" type="button" data-toggle="collapse" data-target="#navbarText" aria-controls="navbarText" aria-expanded="false" aria-label="Toggle navigation">
              <span class="navbar-toggler-icon"></span>
            </button>
            <ul class="navbar-nav mr-auto">
            """] + [
                    '<li class="nav-item {is_active}"><a class="nav-link" href="{link}">{label}</a></li>'.format(
                        is_active="active" if link == cherrypy.request.path_info else "",
                        link=link,
                        label=label)
                    for label, link in nav_links
                    ] + [
            '</ul>',
            '<span class="navbar-text">',
                self.logout_link() if self.is_authenticated() else self.login_link(),
            '</span>',
            '</div></nav>']
        return "\n" + "\n".join(headers)


    def toggleBranchUnderTestLink(self, branch):
        icon = "octicon-triangle-right"
        hover_text = "%s testing this branch" % ("Pause" if branch.isUnderTest else "Start")
        button_style = "btn-xs " + ("btn-primary active" if branch.isUnderTest else "btn-outline-dark")
        
        return HtmlGeneration.Link(
            "/toggleBranchUnderTest?" + 
                urllib.urlencode({'repo': branch.repo.name, 'branchname':branch.branchname, 'redirect': self.redirect()}),
            '<span class="octicon %s" aria-hidden="true" style="horizontal-align:center"></span>' % icon,
            is_button=True,
            button_style=self.disable_if_cant_write(button_style),
            hover_text=hover_text
            )

    def toggleCommitUnderTestLink(self, commit):
        actual_priority = commit.userPriority > 0

        icon = "octicon-triangle-right"
        hover_text = "%s testing this commit" % ("Pause" if actual_priority else "Start")
        button_style = "btn-xs " + ("btn-primary active" if actual_priority else "btn-outline-dark")
        
        return HtmlGeneration.Link(
            "/toggleCommitUnderTest?" + 
                urllib.urlencode({'reponame': commit.repo.name, 'hash':commit.hash, 'redirect': self.redirect()}),
            '<span class="octicon %s" aria-hidden="true"></span>' % icon,
            is_button=True,
            button_style=self.disable_if_cant_write(button_style),
            hover_text=hover_text
            )

    def toggleCommitUnderTest(self, reponame, hash, redirect):
        with self.testManager.transaction_and_lock():
            repo = self.testManager.database.Repo.lookupOne(name=reponame)
            commit = self.testManager.database.Commit.lookupAny(repo_and_hash=(repo, hash))

            self.testManager._setCommitUserPriority(commit, 1 if not commit.userPriority else 0)

        raise cherrypy.HTTPRedirect(redirect)

    def toggleBranchUnderTest(self, repo, branchname, redirect):
        with self.testManager.transaction_and_lock():
            branch = self.testManager.database.Branch.lookupOne(reponame_and_branchname=(repo, branchname))
            self.testManager.toggleBranchUnderTest(branch)

        raise cherrypy.HTTPRedirect(redirect)

    def redirect(self):
        qs = cherrypy.request.query_string

        return cherrypy.request.path_info + ("?" if qs else "") + qs

    def branchesGrid(self, repoName):
        t0 = time.time()
        with self.testManager.database.view():
            lock_time = time.time()
            repo = self.testManager.database.Repo.lookupOne(name=repoName)

            branches = self.testManager.database.Branch.lookupAll(repo=repo)
            
            def hasTests(b):
                if not b.head or not b.head.data:
                    return False
                if b.head.data.testDefinitions or (
                        b.head.data.testDefinitionsError != "No test definition file found."
                        and not b.head.data.testDefinitionsError.startswith("Commit old")
                        ):
                    return True
                return False

            branches = sorted(branches, key=lambda b: (not hasTests(b), b.branchname))

            grid = [["TEST", "BRANCH NAME", "TOP COMMIT"]]

            lastBranch = None
            for branch in branches:
                if lastBranch is not None and not hasTests(branch) and hasTests(lastBranch):
                    grid.append(["&nbsp;"])
                lastBranch = branch

                row = []
                grid.append(row)

                row.append(self.toggleBranchUnderTestLink(branch))
                row.append(self.branchLink(branch))

                if branch.head and branch.head.data:
                    row.append(self.commitLink(branch.head))
                else:
                    row.append(HtmlGeneration.lightGrey("loading"))

            return grid

    def pinGridWithUpdateButtons(self, branch):
        lines = [["status", "refname", "Pinned to"]]

        for refname, repoRef in sorted(branch.head.data.repos.iteritems()):
            if repoRef.matches.Pin:
                lines.append(
                    [self.renderPinUpdateLink(branch, refname, repoRef),
                    refname, 
                    self.renderPinReference(refname, repoRef)
                    ])

        return lines
    
    def renderPinReference(self, reference_name, repoRef, includeName=False):
        if includeName:
            preamble = reference_name + "-&gt;"
        else:
            preamble = ""

        repoName = "/".join(repoRef.reference.split("/")[:-1])
        commitHash = repoRef.reference.split("/")[-1]

        repo = self.testManager.database.Repo.lookupAny(name=repoName)
        if not repo:
            return preamble + HtmlGeneration.lightGreyWithHover(repoRef.reference, "Can't find repo %s" % repoName)

        commit = self.testManager.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))
        if not commit:
            return preamble + HtmlGeneration.lightGreyWithHover(repoRef.reference[:--30], "Can't find commit %s" % commitHash[:10])

        branches = {k.branchname: v for k,v in self.testManager.commitFindAllBranches(commit).iteritems()}

        if repoRef.branch not in branches:
            return preamble + self.commitLink(
                commit, 
                textIsSubject=False,
                hoverOverride="Reference to %s has diverged with branch %s" % (repoRef.reference, repoRef.branch)
                ).render()

        return preamble + self.wellNamedCommitLinkAsStr(
            commit, 
            self.testManager.database.Branch.lookupOne(reponame_and_branchname=(repo.name, repoRef.branch)),
            branches[repoRef.branch]
            )

    @HtmlWrapper
    def deployments(self):
        grid = HtmlGeneration.grid(self.deploymentsGrid())
        
        return grid

    def branchesLink(self, reponame, text=None):
        return HtmlGeneration.link(text or reponame, self.branchesUrl(reponame))

    def branchesUrl(self, reponame):
        return self.address + "/branches?" + urllib.urlencode({'repoName':reponame})

    def deploymentsGrid(self):
        with self.testManager.database.view():
            deployments = sorted(
                self.testManager.database.Deployment.lookupAll(isAlive=True),
                key=lambda d:d.createdTimestamp
                )
            
            grid = [["REPO", "COMMIT", "TEST", "BOOTED AT", "UP FOR", "CLIENTS", "", ""]]

            for d in deployments:
                row = []

                commit = d.test.commitData.commit
                repo = commit.repo

                row.append(
                    HtmlGeneration.link(repo.name, self.branchesUrl(repo.name))
                    )

                row.append(
                    self.commitLink(commit)
                    )

                row.append(d.test.testDefinition.name)

                row.append(time.asctime(time.gmtime(d.createdTimestamp)))

                row.append(secondsUpToString(time.time() - d.createdTimestamp))

                row.append(str(self.testManager.streamForDeployment(d._identity).clientCount()))

                row.append(self.connectDeploymentLink(d))

                row.append(
                    HtmlGeneration.Link(
                        self.address + "/shutdownDeployment?deploymentId=" + d._identity,
                        "shutdown", 
                        is_button=True,
                        button_style=self.disable_if_cant_write('btn-primary btn-xs')
                        )
                    )

                grid.append(row)

            return grid

    def connectDeploymentLink(self, d):
        return HtmlGeneration.Link( 
            self.address + "/terminalForDeployment?deploymentId=" + d._identity,
            "connect",
            is_button=True,
            new_tab=True,
            button_style=self.disable_if_cant_write('btn-primary btn-xs')
            )

    def shutdownDeploymentLink(self, d):
        return HtmlGeneration.Link( 
            self.address + "/shutdownDeployment?deploymentId=" + d._identity,
            "shutdown",
            is_button=True,
            new_tab=True,
            button_style=self.disable_if_cant_write('btn-primary btn-xs')
            )

    def shutdownDeployment(self, deploymentId):
        self.testManager.shutdownDeployment(str(deploymentId), time.time())

        raise cherrypy.HTTPRedirect(self.address + "/deployments")

    @HtmlWrapper
    def repos(self, groupings=None):
        headers, grid = self.reposGrid(groupings)

        grid = HtmlGeneration.grid(headers+grid, header_rows=len(headers))
        
        return grid

    def primaryBranchForRepo(self, repo):
        branches = [b for b in self.testManager.database.Branch.lookupAll(repo=repo)
            if b.branchname.endswith("master-looper")]

        if len(branches) == 1:
            return branches[0]

        for branchname in ["master", "svn-master"]:
            master = self.testManager.database.Branch.lookupAny(reponame_and_branchname=(repo.name, branchname))
            if master:
                return master

    def allTestsForCommit(self, commit):
        if not commit.data:
            return []
        return self.testManager.database.Test.lookupAll(commitData=commit.data)

    def bestCommitForBranch(self, branch):
        if branch is None or branch.head is None or not branch.head.data:
            return None, None

        if branch.repo.commitsWithTests == 0:
            return branch.head, ""

        c = branch.head
        commits = []
        lookbacks = 0

        while not self.allTestsHaveRun(c):
            if c.data and c.data.parents:
                c = c.data.parents[0]
                lookbacks += 1
            else:
                #we're at the end. Take the top commit
                return branch.head, ""

        return c, "" if not lookbacks else "~" + str(lookbacks)

    def allTestsHaveRun(self, commit):
        if not commit.data:
            return False

        tests = self.testManager.database.Test.lookupAll(commitData=commit.data)
        if not tests:
            return False

        for test in tests:
            if not test.testDefinition.matches.Deployment:
                if test.totalRuns == 0 or test.priority.matches.WaitingToRetry:
                    return False

        return True

    def reposGrid(self, groupingInstructions):
        if not groupingInstructions:
            groupingInstructions = None
        else:
            groupingInstructions = json.loads(groupingInstructions)

        with self.testManager.database.view():
            repos = self.testManager.database.Repo.lookupAll(isActive=True)
            
            repos = sorted(
                repos, 
                key=lambda repo:
                    (repo.commitsWithTests == 0, repo.name)
                )

            best_branch = {}
            test_rows = {}
            best_commit = {}
            best_commit_name = {}

            for r in repos:
                best_branch[r] = self.primaryBranchForRepo(r)

                best_commit[r],best_commit_name[r] = self.bestCommitForBranch(best_branch[r])

                test_rows[r] = self.allTestsForCommit(best_commit[r]) if best_commit[r] else []

            def reposUrlWithGroupings(newInstructions):
                return "/repos?" + urllib.urlencode({'groupings':newInstructions} if newInstructions else {})

            renderer = TestGridRenderer(test_rows, list(repos), groupingInstructions)

            grid_headers = renderer.getGridHeaders(reposUrlWithGroupings)

            for additionalHeader in reversed(["REPO NAME", "BRANCH COUNT", "COMMITS", "PRIMARY BRANCH"]):
                grid_headers = [[""] + g for g in grid_headers]
                grid_headers[-1][0] = additionalHeader

            grid = []
            last_repo = None
            for repo in repos:
                if last_repo and last_repo.commitsWithTests and not repo.commitsWithTests:
                    grid.append([""])
                last_repo = repo

                branches = self.testManager.database.Branch.lookupAll(repo=repo)

                if best_commit[repo] and best_commit[repo].userPriority:
                    testRow = renderer.render_row(repo, reposUrlWithGroupings)
                else:
                    testRow = [""] * len(renderer.columnsInOrder())

                grid.append([
                    HtmlGeneration.link(repo.name, "/branches?" + urllib.urlencode({'repoName':repo.name})),
                    str(len(branches)),
                    str(repo.commits),
                    self.wellNamedCommitLinkAsStr(best_commit[repo], best_branch[repo], best_commit_name[repo]) if best_commit[repo] else ""
                    ] + testRow)

            return grid_headers, grid

    @HtmlWrapper
    def branches(self, repoName):
        grid = HtmlGeneration.grid(self.branchesGrid(repoName))
        
        refresh_branches = markdown.markdown(
            "### Refresh Branches button"
            ).replace("button", 
                HtmlGeneration.Link(
                    "/refresh?" + urllib.urlencode({"redirect": self.redirect()}),
                    '<span class="octicon octicon-sync " aria-hidden="true" />',
                    is_button=True,
                    button_style='btn-outline-dark btn-xs',
                    hover_text='Refresh branches'
                    ).render()
                )

        return refresh_branches + grid

    def toggleBranchTestTargeting(self, reponame, branchname, testType, testGroupsToExpand):
        with self.testManager.database.view():
            branch = self.testManager.database.Branch.lookupOne(reponame_and_branchname=(reponame, branchname))

            if testType in branch.targetedTestList():
                branch.setTargetedTestList(
                    [x for x in branch.targetedTestList() if x != testType]
                    )
            else:
                branch.setTargetedTestList(
                    branch.targetedTestList() + [testType]
                    )

        raise cherrypy.HTTPRedirect(self.branchUrl(branch))

    def toggleBranchCommitTargeting(self, reponame, branchname, commitHash):
        with self.testManager.database.view():
            branch = self.testManager.database.Branch.lookupOne(reponame_and_branchname=(reponame, branchname))

            if commitHash in branch.targetedCommitIds():
                logging.warn("set to off")
                branch.setTargetedCommitIds(
                    [x for x in branch.targetedCommitIds() if x != commitId]
                    )
            else:
                logging.warn("set to on")
                branch.setTargetedCommitIds(
                    branch.targetedCommitIds() + [commitId]
                    )

        raise cherrypy.HTTPRedirect(self.branchUrl(branch))

    @HtmlWrapper
    def branch(self, reponame, branchname, max_commit_count=100):
        t0 = time.time()
        with self.testManager.database.view():
            branch = self.testManager.database.Branch.lookupAny(reponame_and_branchname=(reponame,branchname))

            if branch is None:
                return self.errorPage("Branch %s/%s doesn't exist" % (reponame, branchname))

            pinGrid = self.pinGridWithUpdateButtons(branch)

            if len(pinGrid) > 1:
                pinContents = (
                    markdown.markdown("## Branch Pins") + 
                    HtmlGeneration.grid(pinGrid)
                    )
            else:
                pinContents = ""

            return (
                markdown.markdown("# Branch [%s](%s) / `%s`\n" % (reponame, self.branchesUrl(reponame), branch.branchname)) + 
                pinContents + 
                self.testDisplayForCommits(
                    reponame,
                    self.testManager.commitsToDisplayForBranch(branch, max_commit_count), 
                    branch
                    )
                )

    def collapseName(self, name):
        return "/".join([p.split(":")[0] for p in name.split("/")])

    def testDisplayForCommits(self, reponame, commits, branch):
        test_env_and_name_pairs = set()

        for c in commits:
            for test in self.testManager.database.Test.lookupAll(commitData=c.data):
                if not test.testDefinition.matches.Deployment:
                    test_env_and_name_pairs.add((test.testDefinition.environment_name, test.testDefinition.name))

        #this is how we will aggregate our tests
        envs_and_collapsed_names = sorted(
            set([(env, self.collapseName(name)) for env, name in test_env_and_name_pairs])
            )

        collapsed_name_environments = []
        for env, name in envs_and_collapsed_names:
            if not collapsed_name_environments or collapsed_name_environments[-1]["content"] != env:
                collapsed_name_environments.append({"content": env, "colspan": 1})
            else:
                collapsed_name_environments[-1]["colspan"] += 1

        grid = [[""] * 3 + collapsed_name_environments + [""] * 4,
                ["COMMIT", "", "(running)"] + 
                [name for env,name in envs_and_collapsed_names] + 
                ["SOURCE", ""]
            ]


        commit_string = ""
        detail_divs = ""

        ids_to_resize = []

        branches = {}

        commits = [c for c in commits if c.data]

        commit_hashes = {c.hash: c for c in commits}
        children = {c.hash: [] for c in commits}
        parents = {}

        for c in commits:
            parents[c.hash] = [p.hash for p in c.data.parents if p.hash in commit_hashes]
            for p in parents[c.hash]:
                children[p].append(c.hash)
        
        for c in commits:
            if not parents[c.hash]:
                branchname = "branch_%s" % len(branches)

                commit_string += 'var %s = gitgraph.branch("%s");\n' % (branchname, branchname)
                branches[c.hash] = branchname

        #we need to walk the commits from bottom to top. E.g. the ones with no parents go first.
        order = {}
        unordered_parents = {h: set(parents[h]) for h in parents}
        edges = [h for h in unordered_parents if not unordered_parents[h]]

        while len(order) < len(commits):
            e = edges.pop()

            order[e] = max([order[p]+1 for p in parents[e]] + [0])

            for c in children[e]:
                unordered_parents[c].discard(e)
                if not unordered_parents[c]:
                    edges.append(c)

        commits = sorted(commits, key=lambda c: order[c.hash])

        for commit_ix, c in enumerate(commits):
            commit_string +=  "//%s -- %s\n" % (commit_ix, c.hash)

            parentsWeHave = parents[c.hash]

            if len(parentsWeHave) == 0:
                #push a commit onto the branch
                our_branch = branches[c.hash]

                commit_string += "%s.commit({sha1: '%s', message: '%s', detailId: 'commit_%s'});\n" % (
                    branches[c.hash],
                    c.hash, 
                    c.data.subject.replace("'", "\\'"),
                    c.hash
                    )

            elif len(parentsWeHave) == 1:
                #push a commit onto the branch
                our_branch = branches[(parentsWeHave[0], c.hash)]

                commit_string += "%s.commit({sha1: '%s', message: '%s', detailId: 'commit_%s'});\n" % (
                    our_branch,
                    c.hash, 
                    c.data.subject.replace("'", "\\'"),
                    c.hash
                    )
            else:
                our_branch = branches[(parentsWeHave[0], c.hash)]
                other_branch = branches[(parentsWeHave[1], c.hash)]

                commit_string += "%s.merge(%s, {sha1: '%s', message: '%s', detailId: 'commit_%s'}).delete();" % (other_branch, our_branch, 
                    c.hash, 
                    c.data.subject.replace("'", "\\'"),
                    c.hash
                    )

            if len(children[c.hash]) == 0:
                #nothing to do - this is terminal
                pass
            elif len(children[c.hash]) == 1:
                #one child gets to use this branch
                branches[(c.hash, children[c.hash][0])] = our_branch
            else:
                #this is a fork - one child gets to use the branch, and everyone else needs to get a fork
                branches[(c.hash, children[c.hash][0])] = our_branch
                for other_child in children[c.hash][1:]:
                    branchname = "branch_%s" % len(branches)

                    commit_string += 'var %s = %s.branch("%s");\n' % (branchname, our_branch, branchname)

                    branches[(c.hash, other_child)] = branchname


        for c in reversed(commits):
            gridrow = self.getBranchCommitRow(branch, c, envs_and_collapsed_names)

            grid.append(gridrow)

        grid = HtmlGeneration.grid(grid, header_rows=2, rowHeightOverride=33)
        
        canvas = HtmlGeneration.gitgraph_canvas_setup(commit_string, grid)

        return detail_divs + canvas


    @staticmethod
    def gitGraph(depth, symbol):
        return HtmlGeneration.BoldTag(
            symbol if depth == 0 else
            (HtmlGeneration.whitespace*2).join('|' for _ in xrange(depth)) +
            HtmlGeneration.whitespace*2 + symbol
            )


    @staticmethod
    def currentUrl(remove_query_params=None):
        if remove_query_params is None:
            return cherrypy.url(qs=cherrypy.request.query_string).replace('http://', 'https://')

        query_string = cherrypy.lib.httputil.parse_query_string(
            cherrypy.request.query_string
            )
        return cherrypy.url(
            qs="&".join("%s=%s" % (k, v)
                        for k, v in query_string.iteritems()
                        if k not in remove_query_params)
            ).replace('http://', 'https://')

    def getBranchCommitRow(self,
                           branch,
                           commit,
                           envs_and_collapsed_names):
        row = [self.commitLink(commit)]

        all_tests = self.testManager.database.Test.lookupAll(commitData=commit.data)

        running = self.testManager.totalRunningCountForCommit(commit)

        if all_tests:
            row.append(self.toggleCommitUnderTestLink(commit))
        else:
            row.append("")

        if running:
            row.append(str(running))
        else:
            row.append("")
        
        tests_by_name = {(env,name): [] for env, name in envs_and_collapsed_names}
        if commit.data:
            for t in all_tests:
                if not t.testDefinition.matches.Deployment:
                    env_name_pair = (t.testDefinition.environment_name, 
                            self.collapseName(t.testDefinition.name))
                    tests_by_name[env_name_pair].append(t)
        
        for env, name in envs_and_collapsed_names:
            row.append(TestSummaryRenderer(self, tests_by_name[env, name]).renderSummary())

        row.append(self.sourceLinkForCommit(commit))
        
        row.append(
            HtmlGeneration.lightGrey("waiting to load tests") 
                    if not commit.data
            else HtmlGeneration.lightGrey("invalid test file") 
                    if commit.data.testDefinitionsError
            else self.clearCommitIdLink(commit)
            )

        return row

    def renderPinUpdateLink(self, branch, reference_name, repoRef):
        if repoRef.auto:
            return HtmlGeneration.lightGrey("marked auto")
        else:
            commit = branch.head

            targetRepoName = "/".join(repoRef.reference.split("/")[:-1])

            target_branch = self.testManager.database.Branch.lookupAny(reponame_and_branchname=(targetRepoName,repoRef.branch))
            
            if not target_branch:
                return HtmlGeneration.lightGrey("unknown branch %s" % repoRef.branch)

            if target_branch.head.hash == repoRef.reference.split("/")[-1]:
                return HtmlGeneration.lightGrey("up to date")

            message = "push commit updating pin of %s from %s to %s" % (reference_name, target_branch.head.hash, repoRef.reference.split("/")[-1])

            params = {
                "redirect": self.redirect(), 
                "repoName": commit.repo.name,  
                "branchName": branch.branchname,
                "ref": reference_name
                }

            return ('<a href="/updateBranchPin?' + urllib.urlencode(params) + '" title="' + message + '">'
                '<span class="octicon octicon-sync " aria-hidden="true" />'
                '</a>')


    def updateBranchPin(self, repoName, branchName, ref, redirect):
        with self.testManager.transaction_and_lock():
            branch = self.testManager.database.Branch.lookupAny(reponame_and_branchname=(repoName, branchName))

            if not branch:
                return self.errorPage("Unknown branch %s/%s" % (repoName, branchName))
            
            self.testManager._updateBranchPin(branch, ref, produceIntermediateCommits=False)

            self.testManager._updateBranchTopCommit(branch)

            if branch.head and not branch.head.data:
                self.testManager._updateCommitData(branch.head)

            raise cherrypy.HTTPRedirect(redirect)

    @HtmlWrapper
    def eventLogs(self):
        return self.generateEventLogHtml(1000)

    def generateEventLogHtml(self, maxMessageCount=10):
        messages = self.eventLog.getTopNLogMessages(maxMessageCount)

        return markdown.markdown("## Most recent actions:\n\n") + HtmlGeneration.grid(
            [["Date", "user", "Action"]] +
            [[msg["date"], msg["user"], msg["message"]] for msg in reversed(messages)]
            )
