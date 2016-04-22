import cherrypy
import dateutil.parser
import itertools
import math
import os
import sys
import time
import logging
import threading
import markdown
import simplejson
import urllib
import pytz
from math import ceil

import test_looper.server.Github as Github
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.PerformanceDataset as PerformanceDataset
import test_looper.server.TestLooperHttpServerEventLog as TestLooperHttpServerEventLog

import test_looper.server.v2API as v2API
import traceback

time.tzset()


def stringifyDateToLocalTz(date):
    tzString = os.getenv('TZ')
    if tzString:
        return str(date.astimezone(pytz.timezone(tzString)))
    return str(date)



def joinLinks(linkList):
    res = ""

    for l in linkList:
        if res:
            res = res + ", "
        res = res + l

    return res


class TestLooperHttpServer(object):
    def __init__(self,
                 testManager,
                 ec2Factory,
                 testLooperMachines,
                 src_ctrl,
                 test_looper_webhook_secret,
                 testLooperBranch=None,
                 httpPortOverride=None,
                 disableAuth=False):
        """Initialize the TestLooperHttpServer

        testManager - a TestManager.TestManager object
        httpPortOverride - the port to listen on for http requests
        disableAuth - should we disable all authentication and be public?
        """

        self.testLooperMachines = testLooperMachines
        self.testLooperServerLogFile = os.getenv("LOG_FILE")
        self.test_looper_branch = testLooperBranch or 'test-looper'
        self.accessTokenHasPermission = {}
        self.testManager = testManager
        self.ec2Factory = ec2Factory
        self.httpPort = httpPortOverride or 80
        self.disableAuth = disableAuth
        self.src_ctrl = src_ctrl
        self.test_looper_webhook_secret = test_looper_webhook_secret
        self.eventLog = (
            TestLooperHttpServerEventLog.TestLooperHttpServerEventLog(testManager.kvStore)
            )
        self.eventLog.addLogMessage("ubuntu", "TestLooper initialized")
        self.defaultCoreCount = 4

    def isAuthenticated(self):
        if self.disableAuth:
            return True

        if 'github_access_token' not in cherrypy.session:
            return False

        token = cherrypy.session['github_access_token']

        if token not in self.accessTokenHasPermission:
            self.accessTokenHasPermission[token] = \
                self.src_ctrl.authorize_access_token(token)

            self.eventLog.addLogMessage(
                self.getCurrentLogin(),
                "Authorization: %s",
                "Granted" if self.accessTokenHasPermission[token] else "Denied"
                )

        if not self.accessTokenHasPermission[token]:
            raise cherrypy.HTTPError(403, "You are not authorized to access this repository")

        return True

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def getCurrentLogin(self):
        if self.disableAuth:
            return "<auth disabled>"

        if 'github_login' not in cherrypy.session:
            assert 'github_access_token' in cherrypy.session

            token = cherrypy.session['github_access_token']

            cherrypy.session['github_login'] = self.src_ctrl.getUserNameFromToken(token)

        return cherrypy.session['github_login']

    def authenticate(self):
        #stash the current url
        origRequest = self.currentUrl()
        cherrypy.session['redirect_after_authentication'] = origRequest
        raise cherrypy.HTTPRedirect(self.src_ctrl.authenticationUrl())

    @cherrypy.expose
    def logout(self):
        cherrypy.session.pop('github_access_token', None)

        raise cherrypy.HTTPRedirect("/")

    @cherrypy.expose
    def githubAuthCallback(self, code):
        # kept for backward compatibility
        return self.oauth_callback(code)

    @cherrypy.expose
    def oauth_callback(self, code):
        access_token = self.src_ctrl.getAccessTokenFromAuthCallbackCode(code)
        if not access_token:
            logging.error("Failed to accquire access token")
            raise cherrypy.HTTPError(401, "Unable to authenticate your session")

        logging.info("Access token is %s", access_token)

        cherrypy.session['github_access_token'] = access_token

        raise cherrypy.HTTPRedirect(
            cherrypy.session['redirect_after_authentication'] if
                'redirect_after_authentication' in cherrypy.session else '/'
            )

    def errorPage(self, errorMessage):
        return self.commonHeader() + "\n" + markdown.markdown("#ERROR\n\n" + errorMessage)

    @cherrypy.expose
    def index(self):
        if not self.isAuthenticated():
            return self.authenticate()

        raise cherrypy.HTTPRedirect("/branches")

    @cherrypy.expose
    def test(self, testId):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            test = self.testManager.getTestById(testId)
            if test is None:
                return self.errorPage("Unknown testid %s" % testId)

            grid = [["ARTIFACT"]]
            for artifactName in self.testResultKeys(testId):
                grid.append([
                    HtmlGeneration.link(
                        artifactName,
                        self.testResultDownloadUrl(testId, artifactName)
                        )
                    ])

            perftestsGrid = []

            perfResults = test.getPerformanceTestResults()
            if perfResults:
                perftestsGrid = [["TEST", "TIME", "METADATA"]]

                for perftest in perfResults:
                    row = []

                    row.append(perftest.name)
                    row.append(
                        "" if perftest.timeElapsed is None else "%.2f" % perftest.timeElapsed
                        )
                    metadata = ""
                    if perftest.metadata is not None:
                        metadata = ", ".join("%s: %s" % (k, v)
                                             for k, v in perftest.metadata.iteritems())
                    row.append(metadata)

                    perftestsGrid.append(row)

            machinesGrid = [["MACHINE", "INTERNAL IP", "SUCCESS", "HEARTBEAT"]]

            for machine in sorted(test.machineToInternalIpMap.keys()):
                row = []

                row.append(machine)
                internalIp = test.machineToInternalIpMap[machine]
                row.append(internalIp)

                if machine in test.machineResults:
                    result = test.machineResults[machine]

                    row.append(str(result.success))
                    row.append("")
                else:
                    row.append("")
                    if machine in test.heartbeat:
                        row.append("%.2f" % (time.time() - test.heartbeat[machine]))
                    else:
                        row.append("<never heartbeated>")

                machinesGrid.append(row)

            commit = self.testManager.commits[test.commitId]
            return (
                self.commonHeader() +
                markdown.markdown("# Test\n") +
                markdown.markdown("Test: %s\n" % testId) +
                ("<br>Branches: %s\n<br>" % joinLinks(
                    self.branchLink(b.branchName) for b in commit.branches).render()
                ) +
                markdown.markdown("## Artifacts\n") +
                HtmlGeneration.grid(grid) + (
                    "<br>" * 3 + markdown.markdown("## Machine Assignments\n") +
                    HtmlGeneration.grid(machinesGrid)
                    ) + (
                        "" if not perftestsGrid else
                        "<br>" * 3 + markdown.markdown("## Performance results\n") +
                        HtmlGeneration.grid(perftestsGrid)
                        )
                )

    @cherrypy.expose
    def testPrioritization(self):
        if not self.isAuthenticated():
            return self.authenticate()

        return (
            self.commonHeader() +
            markdown.markdown("# Test Prioritization List\n") +
            HtmlGeneration.grid(
                self.prioritizationGrid()
                )
            )

    def testResultDownloadUrl(self, testId, key):
        ec2 = self.ec2Factory()
        keys = list(ec2.openTestResultBucket().list(prefix=testId + "/" + key))

        logging.info("Prefix = %s. keys = %s. key = %s", testId, keys, key)

        key = keys[0]

        return key.generate_url(expires_in=300)

    def testResultKeys(self, testId):
        ec2 = self.ec2Factory()
        keys = list(ec2.openTestResultBucket().list(prefix=testId))

        result = []

        for k in keys:
            prefix = testId + '/'
            assert k.name.startswith(prefix)
            result.append(k.name[len(prefix):])

        logging.info("result: %s", result)

        return result

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def prioritizationData(self):
        allTests = []
        with self.testManager.lock:
            commitsAndTests = self.testManager.getPossibleCommitsAndTests()

            candidates = self.testManager.prioritizeCommitsAndTests(commitsAndTests,
                                                                    preferTargetedTests=False)

            commitLevelDict = self.testManager.computeCommitLevels()

            for commitAndTestToRun in candidates:
                commit = commitAndTestToRun.commit
                testName = commitAndTestToRun.testName
                priority = commitAndTestToRun.priority
                test = {}
                test["name"] = testName
                test["priority"] = priority
                test["commitLevel"] = commitLevelDict[commit.commitId]
                test["totalRuns"] = commit.totalNonTimedOutRuns(testName)
                test["running"] = commit.runningCount(testName)
                test["timedOut"] = commit.timeoutCount(testName)
                test["isDeepTest"] = commit.isDeepTest
                test["branches"] = []
                for b in commit.branches:
                    test["branches"].append(b.branchName)
                allTests.append(test)

            return allTests

    def prioritizationGrid(self):
        with self.testManager.lock:
            grid = [["COMMIT", "TEST", "SCORE", "LEVEL", "TOTAL_RUNS", "RUNNING",
                     "TIMED OUT", "SMOKETEST", "BRANCH", "SUBJECT"]]

            commitsAndTests = self.testManager.getPossibleCommitsAndTests()

            candidates = self.testManager.prioritizeCommitsAndTests(commitsAndTests,
                                                                    preferTargetedTests=False)

            commitLevelDict = self.testManager.computeCommitLevels()

            for commitAndTestToRun in candidates:
                commit = commitAndTestToRun.commit
                testName = commitAndTestToRun.testName
                priority = commitAndTestToRun.priority
                grid.append([
                    self.commitLink(commit),
                    testName,
                    priority,
                    commitLevelDict[commit.commitId],
                    commit.totalNonTimedOutRuns(testName),
                    commit.runningCount(testName),
                    commit.timeoutCount(testName),
                    str(not commit.isDeepTest),
                    joinLinks(self.branchLink(b.branchName) for b in commit.branches),
                    self.subjectLinkForCommit(commit)
                    ])

            return grid

    @staticmethod
    def commitLink(commit, failuresOnly=False, testName=None, length=20):
        commitId = commit if isinstance(commit, basestring) else commit.commitId
        extras = {}

        if failuresOnly:
            extras["failuresOnly"] = 'true'
        if testName:
            extras["testName"] = testName

        return HtmlGeneration.link(
            commitId[:length],
            "/commit/" + commitId + ("?" if extras else "") + urllib.urlencode(extras),
            hover_text=None if isinstance(commit, basestring) else commit.subject
            )

    @staticmethod
    def branchLink(branch):
        return HtmlGeneration.link(branch, "/branch?branchName=%s" % branch)

    @staticmethod
    def clearBranchLink(branch, redirect):
        return HtmlGeneration.link(
            "[clear]",
            "/clearBranch?" + urllib.urlencode({'branch':branch, 'redirect': redirect})
            )

    @staticmethod
    def clearCommitIdLink(commitId, redirect):
        return HtmlGeneration.link(
            "[clear]",
            "/clearCommit?" + urllib.urlencode({'commitId': commitId, 'redirect': redirect})
            )

    def subjectLinkForCommit(self, commit):
        return HtmlGeneration.link(commit.subject, self.src_ctrl.commit_url(commit.commitId))

    @cherrypy.expose
    def clearCommit(self, commitId, redirect):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            self.testManager.clearCommitId(commitId)

        raise cherrypy.HTTPRedirect(redirect)

    @cherrypy.expose
    def clearBranch(self, branch, redirect=None):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            commits = self.testManager.branches[branch].commits

            for c in commits:
                self.testManager.clearCommitId(c)

        if redirect is not None:
            raise cherrypy.HTTPRedirect(redirect)

    @cherrypy.expose
    def machines(self):
        if not self.isAuthenticated():
            return self.authenticate()

        ec2 = self.ec2Factory()
        instancesByIp = {
            i.ip_address or i.private_ip_address: i
            for i in  ec2.getLooperInstances()
            }

        spotRequests = ec2.getLooperSpotRequests()

        with self.testManager.lock:
            grid = [["MACHINE", "PING", "STATE", "TYPE", "SPOT_REQ_ID",
                     "SPOT_REQUEST_STATE", ""]]

            allMachineIds = set(i for i in self.testManager.mostRecentTouchByMachine.keys())

            allMachineIds = allMachineIds.union(set(instancesByIp.keys()))

            def rankMachineId(m):
                if m in instancesByIp:
                    return (0, m)
                else:
                    return (1, m)

            allMachineIds = sorted([i for i in allMachineIds if i is not None],
                                   key=rankMachineId)

            for machineId in allMachineIds:
                row = []

                row.append(self.machineLink(machineId))

                if machineId in instancesByIp:
                    instance = instancesByIp[machineId]
                    if machineId in self.testManager.mostRecentTouchByMachine:
                        pingTime = self.testManager.mostRecentTouchByMachine[machineId]
                        row.append("%.2f" % (time.time() - pingTime))
                    else:
                        row.append("")

                    row.append(str(instance.state))
                    row.append(instance.instance_type)
                    row.append(str(instance.spot_instance_request_id))

                    if instance.spot_instance_request_id in spotRequests:
                        row.append(str(spotRequests[instance.spot_instance_request_id].status))
                    else:
                        row.append("")

                    row.append(
                        HtmlGeneration.link(
                            "[terminate]",
                            "/terminateMachine?machineId=" + machineId
                            )
                        )
                else:
                    row.append("")
                    row.append("<shut down>")

                grid.append(row)

            header = """# All Machines\n"""
            return self.commonHeader() + markdown.markdown(header) + HtmlGeneration.grid(grid)

    @cherrypy.expose
    def terminateMachine(self, machineId):
        if not self.isAuthenticated():
            return self.authenticate()

        ec2 = self.ec2Factory()
        instancesByIp = {
            i.ip_address or i.private_ip_address: i
            for i in  ec2.getLooperInstances()
            }

        if machineId not in instancesByIp:
            return self.errorPage("Unknown machine %s" % machineId)

        instancesByIp[machineId].terminate()

        raise cherrypy.HTTPRedirect("/machines")

    @cherrypy.expose
    def machine(self, machineId):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            tests = []

            for commit in self.testManager.commits.values():
                for test in commit.testsById.values():
                    if test.machine == machineId:
                        tests.append(test)

            sortedTests = sorted(
                tests,
                key=lambda test: test.startTime(),
                reverse=True
                )

            grid = self.gridForTestList_(sortedTests)

            header = """### Machine %s\n""" % machineId

            return self.commonHeader() + markdown.markdown(header) + HtmlGeneration.grid(grid)

    @staticmethod
    def mean_and_stddev(values):
        if values is None:
            return None, None
        mean = float(sum(values))/len(values)
        stddev = (sum((v - mean)**2 for v in values)/len(values)) ** 0.5
        return mean, stddev



    @cherrypy.expose
    def commit(self, commitId, failuresOnly=False, testName=None):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            if commitId not in self.testManager.commits:
                commit = self.testManager.getCommitByCommitId(commitId)
                #return self.commonHeader() + markdown.markdown(
                    #"## Commit %s doesn't exist." % commitId
                    #)
            else:
                commit = self.testManager.commits[commitId]

            sortedTests = sorted(
                commit.testsById.values(),
                key=lambda test: test.startTime(),
                reverse=True
                )

            if failuresOnly:
                sortedTests = [x for x in sortedTests if x.failed()]

            if testName is not None:
                sortedTests = [x for x in sortedTests if x.testName == testName]

            perf_grid = [['NAME', 'RUNS', 'TIME (MEAN)', 'TIME (STDDEV)', 'UNITS (MEAN)',
                          'UNITS (STDDEV)']]
            perf_summary = self.summarizePerfResults(sortedTests)
            for name, summary in sorted(perf_summary.iteritems()):
                mean_time, stddev_time = summary['time']
                mean_units, stddev_units = summary['units']
                perf_grid.append([name,
                                  summary['count'],
                                  self.float_to_str(mean_time),
                                  self.float_to_str(stddev_time),
                                  self.float_to_str(mean_units),
                                  self.float_to_str(stddev_units)])


            grid = self.gridForTestList_(sortedTests, commit=commit, failuresOnly=failuresOnly)

            header = """## Commit %s\n""" % commitId
            for b in commit.branches:
                header += """### Branch: %s\n""" % self.branchLink(b.branchName).render()
            header += """### Subject: %s\n""" % self.subjectLinkForCommit(commit).render()

            if failuresOnly:
                header += "showing failures only. %s<br/><br/>" % \
                    self.commitLink(commit,
                                    False,
                                    testName).withTextReplaced("Show all test results").render()
            else:
                header += "showing both successes and failures. %s<br/><br/>" % \
                    self.commitLink(commit,
                                    True,
                                    testName).withTextReplaced("Show only failures").render()

            if testName:
                header += "showing only %s tests. %s<br/>" % (
                    testName,
                    self.commitLink(commit,
                                    failuresOnly,
                                    None).withTextReplaced("Show all tests").render()
                    )

            perfSection = ""
            if len(perf_summary) > 0:
                header += "Jump to %s<br/>" % HtmlGeneration.Link(self.currentUrl() + "#perf",
                                                                  "Performance Results").render()
                perfSection = ('<br/><br/><p id="perf" />' +
                               markdown.markdown("### Performance Results") +
                               HtmlGeneration.grid(perf_grid)
                              )


            return self.commonHeader() + markdown.markdown(header) + HtmlGeneration.grid(grid) + \
                perfSection

    def gridForTestList_(self, sortedTests, commit=None, failuresOnly=False):
        grid = [["TEST", "TYPE", "RESULT", "STARTED", "MACHINE", "ELAPSED (MIN)",
                 "SINCE LAST HEARTBEAT (SEC)"]]

        for test in sortedTests:
            row = []

            row.append(HtmlGeneration.link(str(test.testId)[:20], "/test?testId=" + test.testId))

            if commit is None:
                row.append(test.testName)
            else:
                row.append(
                    self.commitLink(commit,
                                    failuresOnly=failuresOnly,
                                    testName=test.testName).withTextReplaced(test.testName)
                    )

            row.append(test.status())

            elapsed = None
            if test.startTime():
                row.append(time.ctime(test.startTime()))

                elapsed = test.minutesElapsed()
            else:
                row.append("")

            row.append(self.machineLink(test.machine))

            row.append("" if elapsed is None else "%.2f" % elapsed)

            timeSinceHB = test.timeSinceHeartbeat()

            if test.status() in ('failed', 'passed'):
                timeSinceHB = None

            row.append(str("%.2f" % timeSinceHB) if timeSinceHB is not None else "")

            grid.append(row)

        return grid

    @staticmethod
    def machineLink(machine):
        return HtmlGeneration.link(machine, "/machine?machineId="+machine)

    def commonHeader(self):
        headers = []
        headers.append(
            '<div align="right"><h5><a href="/logout">Logout [%s]</a></h5></div>' % \
                self.getCurrentLogin()
            )
        nav_links = [
            ('Branches', '/branches'),
            ('Test Queue', '/testPrioritization'),
            ('Spot Requests', '/spotRequests'),
            ('Workers', '/machines'),
            ('Activity Log', '/eventLogs')
            ]
        headers += ['<ul class="nav nav-pills">'] + [
            '<li role="presentation" class="{is_active}"><a href="{link}">{label}</a></li>'.format(
                is_active="active" if link == cherrypy.request.path_info else "",
                link=link,
                label=label)
            for label, link in nav_links
            ] + ['</ul>']
        return HtmlGeneration.headers + "\n" + "\n".join(headers)


    @staticmethod
    def toggleBranchDeeptestingLink(branch):
        return HtmlGeneration.link(
            "[test]" if branch.isDeepTest else "[%s]" % HtmlGeneration.pad("", 5),
            "/toggleBranchDeeptest?branchName=" + branch.branchName
            )

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def toggleBranchDeeptest_api(self, branchName):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            branch = self.testManager.branches[branchName]
            branch.setIsDeepTest(not branch.isDeepTest)

        return branch.isDeepTest

    @cherrypy.expose
    def toggleBranchDeeptest(self, branchName):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            branch = self.testManager.branches[branchName]
            branch.setIsDeepTest(not branch.isDeepTest)

        raise cherrypy.HTTPRedirect("/branches")


    @cherrypy.expose
    @cherrypy.tools.json_out()
    def getAllBranches(self):
        with self.testManager.lock:
            branches = self.testManager.distinctBranches()
            return [b for b in branches]

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def branchesData(self):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            branches = self.testManager.distinctBranches()
            allBranches = []
            for b in sorted(branches):
                branchData = {}
                branchData["name"] = b
                branch = self.testManager.branches[b]
                commits = branch.commits.values()

                branchData["commitCount"] = len(commits)
                branchData["isDeepTest"] = branch.isDeepTest
                if commits:
                    branchData["running"] = sum([c.totalRunningCount() for c in commits])

                    passes = sum([c.fullPassesCompleted() for c in commits])
                    branchData["passes"] = passes
                    branchData["totalRuns"] = sum([c.totalCompletedTestRuns() for c in commits])

                    passRate = sum([
                        c.fullPassesCompleted() * c.passRate() for c in commits
                        ])
                    branchData["passRate"] = passRate


                    if passes > 0:
                        ratio = passRate / passes
                    else:
                        ratio = 0.0

                    ratioFormatted = HtmlGeneration.errRateAndTestCount(passes, passRate)
                    branchData["failureRate"] = ratioFormatted

                    intervalLow, intervalHigh = commits[0].wilsonScoreInterval(ratio, passes)

                    branchData["intervalLow"] = round((1 - intervalLow) * 100, 2)
                    branchData["intervalHigh"] = round((1 - intervalHigh) * 100, 2)

                allBranches.append(branchData)

        return allBranches

    def branchesGrid(self):
        with self.testManager.lock:
            branches = self.testManager.distinctBranches()

            grid = [["TEST", "BRANCH NAME", "", "COMMIT COUNT", "RUNNING",
                     "FULL TEST PASSES", "TOTAL TESTS"]]

            for b in sorted(branches):
                branch = self.testManager.branches[b]
                commits = branch.commits.values()

                row = []
                row.append(self.toggleBranchDeeptestingLink(branch))
                row.append(HtmlGeneration.link(b, "/branch?branchName=" + b))
                row.append(HtmlGeneration.link("[perf]", "/branchPerformance?branchName=" + b))
                row.append(str(len(commits)))

                if commits:
                    row.append(str(sum([c.totalRunningCount() for c in commits])))

                    passes = sum([c.fullPassesCompleted() for c in commits])

                    totalRuns = sum([c.totalCompletedTestRuns() for c in commits])

                    row.append(
                        HtmlGeneration.pad(str(passes), 5) + self.clearBranchLink(b, "/branches")
                        )

                    row.append(str(totalRuns))

                grid.append(row)

            return grid

    @cherrypy.expose
    def branches(self):
        if not self.isAuthenticated():
            return self.authenticate()

        grid = HtmlGeneration.grid(self.branchesGrid())
        grid += "<pre><code>[%s]</code></pre>" % (
            HtmlGeneration.link("stop all drilling",
                                "/disableAllTargetedTests").render(),
            )

        return self.commonHeader() + grid


    @cherrypy.expose
    def disableAllTargetedTests(self):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            for branch in self.testManager.branches.itervalues():
                branch.setTargetedTestList([])
                branch.setTargetedCommitIds([])

        raise cherrypy.HTTPRedirect("/branches")


    @cherrypy.expose
    @cherrypy.tools.json_out()
    def getAllCommits(self, branchName):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            commits = self.testManager.branches[branchName].commitsInOrder
        return [{"commit": x.commitId, "subject": x.subject} for x in commits]


    @cherrypy.expose
    @cherrypy.tools.json_out()
    def branchPerformanceData(self, branchName):
        if not self.isAuthenticated():
            return self.authenticate()


        if not branchName in self.testManager.branches:
            return self.errorPage("Branch %s not found" % branchName)

        with self.testManager.lock:
            commits = self.testManager.branches[branchName].commitsInOrder

            dataBySeries = self.testManager.branches[branchName].getPerfDataSummary()

            commitIds = list(reversed([x.commitId for x in commits]))


        data = PerformanceDataset.PerformanceDataset(dataBySeries, commitIds)
        toReturn = data.getObservationData()
        return toReturn


    @cherrypy.expose
    def branchPerformance(self, branchName, prefix=""):
        if not self.isAuthenticated():
            return self.authenticate()

        if not branchName in self.testManager.branches:
            return self.errorPage("Branch %s not found" % branchName)

        with self.testManager.lock:
            commits = self.testManager.branches[branchName].commitsInOrder

            dataBySeries = self.testManager.branches[branchName].getPerfDataSummary()

            commitIds = list(reversed([x.commitId for x in commits]))


        data = PerformanceDataset.PerformanceDataset(dataBySeries, commitIds)

        seriesToDraw = data.groupSeriesData(prefix)

        drillData = [["GROUP", "TEST COUNT", "COUNT DEGRADING", "COUNT IMPROVING"]]

        for seriesName in sorted(seriesToDraw.keys()):
            row = []
            row.append(self.drillBranchPerformanceLink(branchName, seriesName))
            row.append(str(len([x for x in dataBySeries if x.startswith(seriesName)])))
            row.append(
                str(len([
                    x for x in dataBySeries
                    if x.startswith(seriesName) and data.dataBySeries[x].performanceDegrades()
                    ]))
                )
            row.append(
                str(len([
                    x for x in dataBySeries
                    if x.startswith(seriesName) and data.dataBySeries[x].performanceImproves()
                    ]))
                )
            drillData.append(row)

        if prefix:
            currentDrillContext = "Currently showing tests starting with "

            prefixItems = prefix.split(".")

            currentDrillContext += ".".join([
                self.drillBranchPerformanceLink(
                    branchName,
                    ".".join(prefixItems[:ix+1]),
                    prefixItems[ix]
                    ).render()
                for ix in range(len(prefixItems))
                ])

            currentDrillContext += "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;" + \
                self.drillBranchPerformanceLink(branchName, "", "[clear]").render()
        else:
            currentDrillContext = ""

        return (
            "<html>" +
            self.commonHeader() +
            markdown.markdown("#Branch Performance\n\n### branchname").replace(
                "branchname",
                self.branchLink(branchName).render()
                ) +
            currentDrillContext +
            HtmlGeneration.grid(drillData) +
            data.generateChartHtml(branchName, prefix, seriesToDraw) +
            "</html>"
            )

    @staticmethod
    def drillBranchPerformanceLink(branchName, seriesName, display=None):
        return HtmlGeneration.link(
            display if display is not None else seriesName,
            "/branchPerformance?branchName=" + branchName + "&prefix=" + seriesName
            )

    @staticmethod
    def toggleBranchTargetedTestListLink(branch, testType, testGroupsToExpand):
        text = "[X]" if testType in branch.targetedTestList() else "[%s]" % HtmlGeneration.pad('', 2)

        return HtmlGeneration.link(
            text,
            "/toggleBranchTestTargeting?branchName=%s&testType=%s&testGroupsToExpand=%s" % (
                branch.branchName, testType, testGroupsToExpand)
            )

    @staticmethod
    def toggleBranchTargetedCommitIdLink(branch, commitId):
        text = "[X]" if commitId in branch.targetedCommitIds() else "[%s]" % HtmlGeneration.pad('', 2)

        return HtmlGeneration.link(
            text,
            "/toggleBranchCommitTargeting?branchName=%s&commitId=%s" % (
                branch.branchName, commitId)
            )

    @cherrypy.expose
    def toggleBranchTestTargeting(self, branchName, testType, testGroupsToExpand):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            branch = self.testManager.branches[branchName]

            if testType in branch.targetedTestList():
                branch.setTargetedTestList(
                    [x for x in branch.targetedTestList() if x != testType]
                    )
            else:
                branch.setTargetedTestList(
                    branch.targetedTestList() + [testType]
                    )

        raise cherrypy.HTTPRedirect(
            "/branch?branchName=%s&testGroupsToExpand=%s" % (branchName, testGroupsToExpand)
            )


    @cherrypy.expose
    @cherrypy.tools.json_out()
    def toggleBranchTestTargeting_v2(self, branchName, testType):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            branch = self.testManager.branches[branchName]

            if testType in branch.targetedTestList():
                branch.setTargetedTestList(
                    [x for x in branch.targetedTestList() if x != testType]
                    )
            else:
                branch.setTargetedTestList(
                    branch.targetedTestList() + [testType]
                    )

            return testType in branch.targetedTestList()

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def toggleBranchCommitTargeting_v2(self, branchName, commitId):
        if not self.isAuthenticated():
            return self.authenticate()
        logging.warn("branch name: %s, commit id: %s", branchName, commitId)
        with self.testManager.lock:
            branch = self.testManager.branches[branchName]

            if commitId in branch.targetedCommitIds():
                logging.warn("set to off")
                branch.setTargetedCommitIds(
                    [x for x in branch.targetedCommitIds() if x != commitId]
                    )
            else:
                logging.warn("set to on")
                branch.setTargetedCommitIds(
                    branch.targetedCommitIds() + [commitId]
                    )

            return commitId in branch.targetedCommitIds()

    @cherrypy.expose
    def toggleBranchCommitTargeting(self, branchName, commitId):
        if not self.isAuthenticated():
            return self.authenticate()
        logging.warn("branch name: %s, commit id: %s", branchName, commitId)
        with self.testManager.lock:
            branch = self.testManager.branches[branchName]

            if commitId in branch.targetedCommitIds():
                logging.warn("set to off")
                branch.setTargetedCommitIds(
                    [x for x in branch.targetedCommitIds() if x != commitId]
                    )
            else:
                logging.warn("set to on")
                branch.setTargetedCommitIds(
                    branch.targetedCommitIds() + [commitId]
                    )

        raise cherrypy.HTTPRedirect("/branch?branchName=" + branchName)


    @staticmethod
    def readFile(path):
        thisDir = os.path.dirname(__file__)
        filepath = os.path.join(thisDir, path)
        htmlFile = open(filepath, "r")
        result = htmlFile.read()
        htmlFile.close()
        return result

    @cherrypy.expose
    def branch_v2(self):
        if not self.isAuthenticated():
            return self.authenticate()

        return self.readFile('v2/branch.html')

    @cherrypy.expose
    def branchPerformance_v2(self):
        if not self.isAuthenticated():
            return self.authenticate()

        return self.readFile('v2/branchPerformance.html')

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def getTestsForCommits(self, branchName):
        if not self.isAuthenticated():
            return self.authenticate()

        commitIdToTestGroups = {}
        with self.testManager.lock:
            if not branchName in self.testManager.branches:
                raise cherrypy.HTTPError(404, "Branch does not exist")
            branch = self.testManager.branches[branchName]
            commits = branch.commitsInOrder
            for commit in commits:
                ungroupedUniqueTestIds = sorted(list(set(t for t in commit.statsByType)))

                groupedTests = {}
                for test in ungroupedUniqueTestIds:
                    group = test.split('.')[0]
                    if not group in groupedTests:
                        groupedTests[group] = []
                    if test:
                        toAppend = {
                            "name" : test,
                            "isSelected" : test in branch.targetedTestList(),
                            "isPinned" : False
                            }
                        groupedTests[group].append(toAppend)
                commitIdToTestGroups[commit.commitId] = groupedTests
            return commitIdToTestGroups

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def getAllTests(self, branchName):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            if not branchName in self.testManager.branches:
                raise cherrypy.HTTPError(404, "Branch does not exist")
            branch = self.testManager.branches[branchName]
            commits = branch.commitsInOrder
            ungroupedUniqueTestIds = sorted(list(set(t for c in commits for t in c.statsByType)))

            groupedTests = {}
            for test in ungroupedUniqueTestIds:
                group = test.split('.')[0]
                if not group in groupedTests:
                    groupedTests[group] = []
                if test:
                    toAppend = {
                        "name" : test,
                        "isSelected" : test in branch.targetedTestList(),
                        "isPinned" : False
                        }
                    groupedTests[group].append(toAppend)
            return groupedTests

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def getBranchTestData(self, branchName):
        if not self.isAuthenticated():
            return self.authenticate()

        branchCommitData = {}

        allCommitData = []
        with self.testManager.lock:
            if branchName not in self.testManager.branches:
                return "Branch %s doesn't exist" % branchName
            branch = self.testManager.branches[branchName]
            branchCommitData["isDeepTest"] = branch.isDeepTest
            commits = branch.commitsInOrder
            ungroupedUniqueTestIds = sorted(list(set(t for c in commits for t in c.statsByType)))
            lastCommit = None
            commitsInStrand = 0
            testGroups = sorted(list(set(x for x in ungroupedUniqueTestIds)))
            for indexWithinCommitSequence, c in enumerate(commits):
                commitData = {}
                if lastCommit is not None and \
                        lastCommit.parentId != c.commitId or commitsInStrand > 9:
                    commitsInStrand = 0
                else:
                    commitsInStrand += 1

                commitData["id"] = c.commitId
                commitData["totalRunning"] = c.totalRunningCount()
                commitData["errorRate"] = 1.0 - c.passRate()
                commitData["subject"] = c.subject
                commitData["branches"] = [b.branchName for b in c.branches]
                commitData["isSelected"] = c.commitId in branch.targetedCommitIds()

                allTestGroupData = []
                commitData["TestGroups"] = allTestGroupData
                for testGroup in testGroups:
                    testGroupData = {}
                    allTestGroupData.append(testGroupData)
                    testGroupData["name"] = testGroup
                    if testGroups in ungroupedUniqueTestIds:
                        stat = c.testStatByType(testGroups)
                    else:
                        #this is not an accurate calculation. Here we are aggregating tests across
                        #categories. We should be multiplying their pass rates, but in fact we are
                        #averaging their failure rates, pretending that they are independent test
                        #runs.
                        stat = c.testStatByTypeGroup(testGroup)
                    testGroupData["totalElapsedMinutes"] = stat.totalElapsedMinutes
                    testGroupData["runningElapsedMinutes"] = stat.runningElapsedMinutes
                    testGroupData["runningCount"] = stat.runningCount
                    testGroupData["passCount"] = stat.passCount
                    testGroupData["failCount"] = stat.failCount
                    testGroupData["timeoutCount"] = stat.timeoutCount
                    testGroupData["completedCount"] = stat.completedCount

                    errRate = ""
                    errRateVal = 0
                    if stat.completedCount > 0:
                        errRate = HtmlGeneration.errRateAndTestCount(
                            stat.passCount + stat.failCount, stat.passCount
                            )

                        level, direction = branch.commitIsStatisticallyNoticeableFailureRateBreak(
                            c.commitId,
                            testGroup
                            )

                        if level == 0.001:
                            errRate = errRate + " ***"
                        if level == 0.01:
                            errRate = errRate + " **"
                        if level == 0.1:
                            errRate = errRate + " *"


                    testGroupData["errRate"] = errRate
                    errRateVal = self.errRateVal(stat.passCount + stat.failCount, stat.passCount)

                    testGroupData["errRateVal"] = errRateVal

                allCommitData.append(commitData)
                lastCommit = c
        branchCommitData["commitData"] = allCommitData
        return branchCommitData

    @staticmethod
    def errRateVal(testCount, successCount):
        if testCount == 0:
            return 0

        successCount = float(successCount)

        toReturn = 1.0 - successCount / testCount
        return toReturn


    @cherrypy.expose
    def branch(self, branchName, testGroupsToExpand=None, perfprefix=None):
        if not self.isAuthenticated():
            return self.authenticate()

        with self.testManager.lock:
            if branchName not in self.testManager.branches:
                return self.errorPage("Branch %s doesn't exist" % branchName)

            branch = self.testManager.branches[branchName]
            commits = branch.commitsInOrder
            ungroupedUniqueTestIds = sorted(set(t for c in commits for t in c.statsByType))

            testGroupsToTests = {}
            for testName in ungroupedUniqueTestIds:
                group = testName.split(".")[0]
                if group not in testGroupsToTests:
                    testGroupsToTests[group] = []
                testGroupsToTests[group].append(testName)


            testGroupsToExpand = [] if testGroupsToExpand is None else testGroupsToExpand.split(",")
            def appropriateGroup(testName):
                groupPrefix = testName.split(".")[0]
                if groupPrefix in testGroupsToExpand:
                    return testName
                return groupPrefix

            testGroups = sorted(list(set(appropriateGroup(x) for x in ungroupedUniqueTestIds)))
            grid = self.createGridForBranch(branch,
                                            testGroups,
                                            ungroupedUniqueTestIds,
                                            testGroupsToExpand)

            lastCommit = None
            commitsInStrand = 0
            for c in commits:
                if lastCommit is not None and \
                        lastCommit.parentId != c.commitId or commitsInStrand > 9:
                    grid.append([])
                    grid.append([])
                    commitsInStrand = 0
                else:
                    commitsInStrand += 1

                grid.append(self.getBranchCommitRow(branch,
                                                    c,
                                                    testGroups,
                                                    ungroupedUniqueTestIds,
                                                    testGroupsToTests))
                lastCommit = c

            perfGrid = self.createBranchPerformanceGrid(branch, prefix=perfprefix)
            perfSection = '<br/><p id="perf" />' + \
                markdown.markdown("### Performance Results  ") + \
                HtmlGeneration.link(
                    "[clear filters]",
                    self.currentUrl(remove_query_params=['perfprefix'])
                    ).render() + \
                HtmlGeneration.grid(perfGrid)


            header = (
                markdown.markdown("# Branch " + branchName) + "\n\n" +
                "Click " + self.drillBranchPerformanceLink(branchName, "", "here").render()
                + " to see performance statistics for this branch.\n<br><br> "
                "Click any [ ] or [X] to toggle test-drilling. If a test-type and a commit are both"
                " selected within a branch, only the cross section will be tested.<br><br>" +
                "Jump to %s<br/>" % HtmlGeneration.Link(self.currentUrl() + "#perf",
                                                        "Performance Results").render()
                )
            return self.commonHeader() + header + HtmlGeneration.grid(grid, header_rows=2) + perfSection


    def summarizePerfResults(self, tests, prefix=''):
        perf_stats = {}
        for test in tests:
            for perf_test in test.getPerformanceTestResults() or []:
                if not perf_test.name.startswith(prefix):
                    continue

                stats = perf_stats.get(perf_test.name, {})
                stats['count'] = stats.get('count', 0) + 1
                if perf_test.timeElapsed:
                    times = stats.get('time')
                    if times is None:
                        times = []
                        stats['time'] = times
                    times.append(perf_test.timeElapsed)
                if perf_test.metadata and 'n' in perf_test.metadata:
                    units = stats.get('units')
                    if units is None:
                        units = []
                        stats['units'] = units
                    units.append(perf_test.metadata['n'])
                perf_stats[perf_test.name] = stats

        perf_summary = {
            name: {
                'count': stats['count'],
                'time': self.mean_and_stddev(stats.get('time')),
                'units': self.mean_and_stddev(stats.get('units'))
                }
            for name, stats in perf_stats.iteritems()
            }
        return perf_summary


    @staticmethod
    def currentUrl(remove_query_params=None):
        if remove_query_params is None:
            return cherrypy.url(qs=cherrypy.request.query_string)

        query_string = cherrypy.lib.httputil.parse_query_string(
            cherrypy.request.query_string
            )
        return cherrypy.url(
            qs="&".join("%s=%s" % (k, v)
                        for k, v in query_string.iteritems()
                        if k not in remove_query_params)
            )


    def renderPerfSummary(self, summary, prior_summary):
        try:
            mean_time, stddev_time = summary['time']

            if summary['count'] == 1:
                text = "%13.2f" % mean_time if mean_time else ''
            else:
                text = "%3d@ %s &plusmn; %s" % (
                    summary['count'],
                    "%8.2f" % mean_time if mean_time else '',
                    self.float_to_str(stddev_time)
                    )
            if prior_summary is None or prior_summary['count'] < 4:
                return text

            prior_mean, prior_stddev = prior_summary['time']
            if abs(mean_time - prior_mean) < prior_stddev * 4 / (prior_summary['count'] ** .5):
                return text

            if mean_time < prior_mean:
                return HtmlGeneration.greenBacking(text)
            else:
                return HtmlGeneration.redBacking(text)

        except:
            logging.warn("Exception rendering perfSummary: %s", traceback.format_exc())
            return text


    def createBranchPerformanceGrid(self, branch, prefix=None):
        prefix = prefix or ''
        perf_results = {}
        headers = ["TEST"]
        commitIndices = {}
        for commit in branch.commitsInOrder:
            headers.append(
                HtmlGeneration.HtmlElements([
                    HtmlGeneration.HtmlString('  '),
                    self.commitLink(commit, length=8)
                    ])
                )
            commitIndices[commit.commitId] = len(commitIndices)
            commit_perf_summary = self.summarizePerfResults(commit.testsById.itervalues(),
                                                            prefix)
            for name, summary in commit_perf_summary.iteritems():
                test_results = perf_results.get(name)
                if test_results is None:
                    test_results = []
                    perf_results[name] = test_results
                test_results.append((commit.commitId, summary))

        grid = [headers]
        for test_name in sorted(perf_results.iterkeys()):
            testResults = [None] * len(commitIndices)
            for commitId, summary in perf_results[test_name]:
                testResults[commitIndices[commitId]] = summary

            renderedResults = []
            for i in xrange(len(testResults)):
                if testResults[i] is None:
                    renderedResults.append('')
                else:
                    try:
                        priorResult = next(testResults[j]
                                           for j in xrange(i+1, len(testResults))
                                           if testResults[j] is not None)
                    except StopIteration:
                        priorResult = None

                    renderedResults.append(
                        self.renderPerfSummary(testResults[i], priorResult)
                        )

            grid.append([self.perfTestLinks(test_name)] + renderedResults)
        return grid


    @staticmethod
    def perfTestLinks(test_name):
        query_string = cherrypy.lib.httputil.parse_query_string(
            cherrypy.request.query_string
            )

        split_name = test_name.split(".")
        links = []
        for i, segment in enumerate(split_name):
            prefix = ".".join(split_name[:i+1])
            query_string['perfprefix'] = prefix
            links.append(HtmlGeneration.link(
                segment,
                cherrypy.url(
                    qs="&".join("%s=%s" % (k, v) for k, v in query_string.iteritems())
                    )
                ))

        return HtmlGeneration.HtmlElements(
            links[:1] + list(itertools.chain.from_iterable(
                (HtmlGeneration.makeHtmlElement('.'), l) for l in links[1:]
                ))
            )


    @staticmethod
    def float_to_str(f):
        return "%.2f" % f if f else ''


    def createGridForBranch(self,
                            branch,
                            testGroups,
                            ungroupedUniqueTestIds,
                            testGroupsToExpand):
        testHeaders = []
        testGroupExpandLinks = []
        for testGroup in testGroups:
            testGroupPrefix = testGroup.split(".")[0]

            if testGroup in ungroupedUniqueTestIds:
                testHeaders.append(
                    self.toggleBranchTargetedTestListLink(branch,
                                                          testGroup,
                                                          ",".join(testGroupsToExpand))
                    )

                if testGroupPrefix in testGroupsToExpand:
                    testGroupExpandLinks.append(
                        HtmlGeneration.link(
                            testGroupPrefix,
                            "/branch?branchName=%s&testGroupsToExpand=%s" % (
                                branch.branchName,
                                ",".join(set(testGroupsToExpand) - set((testGroupPrefix,)))
                                )
                            ) + (
                                "." + testGroup[len(testGroupPrefix)+1:] if testGroup != testGroupPrefix else ""
                            )
                        )
                else:
                    testGroupExpandLinks.append(testGroup)
            else:
                testHeaders.append("")
                testGroupExpandLinks.append(
                    HtmlGeneration.link(
                        testGroup,
                        "/branch?branchName=%s&testGroupsToExpand=%s" % (
                            branch.branchName,
                            ",".join(testGroupsToExpand + [testGroup])
                            )
                        )
                    )
            testGroupExpandLinks[-1] = HtmlGeneration.pad(testGroupExpandLinks[-1], 20)
            testHeaders[-1] = HtmlGeneration.pad(testHeaders[-1], 20)

        grid = [["", "", "", "", ""] + testHeaders + ["", ""]]
        grid.append(
            ["", "COMMIT", "(running)", "", "FAIL RATE" + HtmlGeneration.whitespace*4] + \
            testGroupExpandLinks + \
            ["SUBJECT", "branch"]
            )
        return grid


    def getBranchCommitRow(self,
                           branch,
                           commit,
                           testGroups,
                           ungroupedUniqueTestIds,
                           testGroupsToTests):
        def anyTestInGroupIsTargetedInCommit(commit, testGroup):
            for group in testGroupsToTests[testGroup]:
                if commit.isTargetedTest(group):
                    return True
            return False

        def allTestsInGroupAreTargetedInCommit(commit, testGroup):
            for group in testGroupsToTests[testGroup]:
                if not commit.isTargetedTest(group):
                    return False
            return True

        row = [self.toggleBranchTargetedCommitIdLink(branch, commit.commitId),
               self.commitLink(commit)]

        row.append(str(commit.totalRunningCount()) if commit.totalRunningCount() != 0 else "")
        row.append(self.clearCommitIdLink(commit.commitId,
                                          "/branch?branchName=" + branch.branchName))
        passRate = commit.passRate()
        row.append(HtmlGeneration.errRate(1.0 - passRate) if passRate is not None else '')

        for testGroup in testGroups:
            if testGroups in ungroupedUniqueTestIds:
                stat = commit.testStatByType(testGroups)
            else:
                #this is not an accurate calculation. Here we are aggregating tests across
                #categories. We should be multiplying their pass rates, but in fact we are
                #averaging their failure rates, pretending that they are independent test
                #runs.
                stat = commit.testStatByTypeGroup(testGroup)

            if stat.completedCount == 0:
                if stat.runningCount == 0:
                    row.append("")
                else:
                    row.append("[%s running]" % stat.runningCount)
            else:
                errRate = HtmlGeneration.errRateAndTestCount(
                    stat.passCount + stat.failCount,
                    stat.passCount
                    )

                #check if this point in the commit-sequence has a statistically different
                #probability of failure from its peers and mark it if so.

                level, direction = branch.commitIsStatisticallyNoticeableFailureRateBreak(
                    commit.commitId,
                    testGroup
                    )

                if level:
                    level = int(round(math.log(level, 10)))
                    errRate = HtmlGeneration.emphasize_probability(errRate, level, direction)

                if stat.failCount and testGroup in ungroupedUniqueTestIds:
                    row.append(
                        self.commitLink(commit,
                                        failuresOnly=True,
                                        testName=testGroup).withTextReplaced(errRate)
                        )
                else:
                    row.append(errRate)
            if testGroup in ungroupedUniqueTestIds:
                if commit.isTargetedTest(testGroup):
                    row[-1] = HtmlGeneration.blueBacking(row[-1])
            else:
                if allTestsInGroupAreTargetedInCommit(commit, testGroup):
                    row[-1] = HtmlGeneration.blueBacking(row[-1])

                if anyTestInGroupIsTargetedInCommit(commit, testGroup):
                    row[-1] = HtmlGeneration.lightGreyBacking(row[-1])

        row.append(self.subjectLinkForCommit(commit))
        row.append(joinLinks(self.branchLink(b.branchName) for b in commit.branches))
        return row


    @cherrypy.expose
    def eventLogs(self):
        if not self.isAuthenticated():
            return self.authenticate()

        return self.commonHeader() + self.generateEventLogHtml(1000)


    @cherrypy.expose
    def toggleAutoProvisioner(self):
        if not self.isAuthenticated():
            return self.authenticate()

        autoProvisionerState = self.testLooperMachines.toggleAutoProvisioner()
        logging.info("toggle auto provisioner, new state: %s", autoProvisionerState)

        raise cherrypy.HTTPRedirect("/spotRequests")

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def getAutoProvisionerState(self):
        return self.testLooperMachines.getAutoProvisionerState()

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def updateAvailabilityZone(self, az):
        logging.info("Update availability zone to: %s", az)
        if not self.isAuthenticated():
            return self.authenticate()
        return self.testLooperMachines.updateAvailabilityZone(az)

    def getCurrentSpotRequestGrid(self, ec2):
        spotRequests = sorted(
            ec2.getLooperSpotRequests().itervalues(),
            key=lambda r: r.price,
            reverse=True
            )

        # group spot instance requests into batches that were requested
        # together
        spotRequestGroups = {}
        for r in spotRequests:
            newGroup = True
            createTime = dateutil.parser.parse(r.create_time)
            instanceType = r.launch_specification.instance_type
            for groupCreateTime, groupInstanceType in spotRequestGroups:
                delta = groupCreateTime - createTime
                if instanceType == groupInstanceType and abs(delta.total_seconds()) < 60:
                    newGroup = False
                    spotRequestGroups[(groupCreateTime, groupInstanceType)].append(r)
                    break
            if newGroup:
                spotRequestGroups[(createTime, instanceType)] = [r]

        grid = [["#", "", "instance type", "# active", "max price", "creation time",
                 "# open", "# failed", "# cancelled", "availability zone"]]
        for i, key in enumerate(sorted(spotRequestGroups.keys())):
            spotRequests = spotRequestGroups[key]
            request = spotRequests[0]

            countsByAvailabilityZone = {}
            countsByState = {}
            for r in spotRequests:
                count = countsByState.get(r.state) or 0
                countsByState[r.state] = count+1
                if r.state == 'active':
                    count = countsByAvailabilityZone.get(r.launched_availability_zone) or 0
                    countsByAvailabilityZone[r.launched_availability_zone] = count+1

            if countsByState.get('cancelled') == len(spotRequests):
                # don't show cancelled requests
                continue

            availabilityZones = ", ".join(
                ["%s: %s" % (az, count) for az, count in countsByAvailabilityZone.iteritems()]
                )

            row = [
                str(i+1),
                HtmlGeneration.link(
                    "[cancel]",
                    "/cancelSpotRequests?" + urllib.urlencode(
                        {'requestIds': ",".join([str(r.id) for r in spotRequests])}
                        )
                    ),
                request.launch_specification.instance_type,
                str(countsByState.get('active', 0)),
                request.price,
                request.create_time,
                str(countsByState.get('open', 0)),
                str(countsByState.get('failed', 0)),
                str(countsByState.get('cancelled', 0)),
                availabilityZones
                ]
            grid.append(row)

        return grid


    def availableInstancesAndCoreCount(self):
        return self.testLooperMachines.availableInstancesAndCoreCount

    def getSpotInstancePriceGrid(self, ec2):
        spotPrices = ["<h2>Spot Instance Prices</h2>"]
        for instanceType in sorted(i[0] for i in self.availableInstancesAndCoreCount()):
            spotPrices.append("<h3>%s</h3>" % instanceType)
            pricesByZone = ec2.currentSpotPrices(instanceType=instanceType)
            spotPrices += [
                "<b>%s</b>: %s&nbsp;&nbsp;&nbsp;" % (zone, price)
                for zone, price in sorted(pricesByZone.iteritems())
                ]

        return spotPrices


    @cherrypy.expose
    @cherrypy.tools.json_out()
    def getSpotInstancePrices(self):
        spotPrices = []
        ec2 = self.ec2Factory()
        for instanceType in [i[0] for i in self.availableInstancesAndCoreCount()]:
            pricesByZone = ec2.currentSpotPrices(instanceType=instanceType)
            for zone, price in pricesByZone.iteritems():
                spotType = {}
                spotType["type"] = instanceType
                spotType["zone"] = zone
                spotType["price"] = price
                spotPrices.append(spotType)
        return spotPrices

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def spotRequestsData(self):
        ec2 = self.ec2Factory()

        spotRequests = sorted(
            ec2.getLooperSpotRequests().itervalues(),
            key=lambda r: r.price,
            reverse=True
            )

        # we need to group the spot requests by date
        requestsByCreateTime = {}

        for spotRequest in spotRequests:
            createTime = spotRequest.create_time
            if not createTime in requestsByCreateTime:
                requestsByCreateTime[createTime] = []
            requestsByCreateTime[createTime].append(spotRequest)

        pricesByTypeAndZone = {}
        for instanceType in [i[0] for i in self.availableInstancesAndCoreCount()]:
            pricesByZone = ec2.currentSpotPrices(instanceType=instanceType)
            pricesByTypeAndZone[instanceType] = pricesByZone


        batchRequests = []
        logging.warn("Prices by type and zone: %s", pricesByTypeAndZone)
        for createTime in requestsByCreateTime:
            requests = requestsByCreateTime[createTime]
            req = requests[0]
            batchRequest = {}
            batchRequest["create_time"] = req.create_time
            batchRequest["price"] = req.price
            batchRequest["type"] = req.type
            batchRequest["valid_from"] = req.valid_from
            batchRequest["valid_until"] = req.valid_until
            batchRequest["launch_group"] = req.launch_group
            az = req.launched_availability_zone
            batchRequest["launched_availability_zone"] = az
            batchRequest["product_description"] = req.product_description
            batchRequest["availability_zone_group"] = req.availability_zone_group
            instanceType = req.launch_specification.instance_type
            batchRequest["instance_type"] = instanceType
            batchRequest["instance_id"] = req.instance_id
            batchRequest["message"] = req.status.message
            ct = len(requests)
            batchRequest["count"] = ct
            fulfilled = len([req for req in requests if req.state == 'active'])
            batchRequest["fulfilled"] = fulfilled
            batchRequest["state"] = req.state
            cost = 0
            if not az is None:
                cost = pricesByTypeAndZone[instanceType][az]
            batchRequest["cost_per_instance"] = cost
            batchRequest["total_cost"] = ceil(cost * fulfilled * 1000) / 1000.0
            requestsByCreateTime[createTime] = batchRequest
            batchRequest["ids"] = [req.id for req in requests]
            batchRequests.append(batchRequest)

        return batchRequests

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def isAutoProvisionerEnabled(self):
        isAutoProvisionerEnabled = self.testLooperMachines.isAutoProvisionerEnabled()
        return isAutoProvisionerEnabled

    def getAddSpotRequestForm(self, ec2):
        pricesByZone = None
        for instanceType in [i[0] for i in self.availableInstancesAndCoreCount()]:
            if pricesByZone is None:
                pricesByZone = ec2.currentSpotPrices(instanceType=instanceType)
                logging.info("Prices by zone: %s", pricesByZone)

        instanceTypeDropDown = HtmlGeneration.selectBox(
            'instanceType',
            [(k, "%s cores (%s)" % (v, k))
             for k, v in self.availableInstancesAndCoreCount()],
            self.defaultCoreCount)
        availabilityZoneDropDown = HtmlGeneration.selectBox(
            'availabilityZone',
            sorted([(az, az) for az in pricesByZone.keys()]),
            '')

        addForm = """
            <form action="/addSpotRequests" method="post">
              <b>Add instances:</b>
              type: %s
              max price: <input type="text" name="maxPrice">
              availability zone: %s
              <input type="submit" value="Add"/>
            </form>
            """ % (instanceTypeDropDown, availabilityZoneDropDown)
        return addForm

    @cherrypy.expose
    def spotRequests(self):
        if not self.isAuthenticated():
            return self.authenticate()

        ec2 = self.ec2Factory()

        clearAll = '<a href="/cancelAllSpotRequests">Cancel all requests</a> '

        grid = self.getCurrentSpotRequestGrid(ec2)

        addForm = self.getAddSpotRequestForm(ec2)

        spotPrices = self.getSpotInstancePriceGrid(ec2)

        return HtmlGeneration.stack(
            self.commonHeader(),
            markdown.markdown("# Spot Requests\n"),
            addForm,
            HtmlGeneration.grid(grid),
            clearAll,
            "".join(spotPrices),
            "<br>"*5 + self.generateEventLogHtml()
            )

    def generateEventLogHtml(self, maxMessageCount=10):
        messages = self.eventLog.getTopNLogMessages(maxMessageCount)

        return markdown.markdown("## Most recent actions:\n\n") + HtmlGeneration.grid(
            [["Date", "user", "Action"]] +
            [[msg["date"], msg["user"], msg["message"]] for msg in reversed(messages)]
            )


    @cherrypy.expose
    @cherrypy.tools.json_out()
    def getNLastEvents(self, eventCount):
        try:
            messages = self.eventLog.getTopNLogMessages(int(eventCount))
        except:
            logging.warn("Failed to get last events: %s", traceback.format_exc())
        return messages

    @cherrypy.expose
    def cancelAllSpotRequests(self, instanceType=None):
        if not self.isAuthenticated():
            return self.authenticate()

        ec2 = self.ec2Factory()
        spotRequests = ec2.getLooperSpotRequests()
        if instanceType is not None:
            spotRequests = {
                k: v for k, v in spotRequests.iteritems() \
                    if v.launch_specification.instance_type == instanceType
                }

        ec2.cancelSpotRequests(spotRequests.keys())

        self.eventLog.addLogMessage(self.getCurrentLogin(), "Canceled all spot requests.")

        raise cherrypy.HTTPRedirect("/spotRequests")

    @cherrypy.expose
    def cancelSpotRequests(self, requestIds):
        if not self.isAuthenticated():
            return self.authenticate()
        requestIds = requestIds.split(',')

        ec2 = self.ec2Factory()
        spotRequests = ec2.getLooperSpotRequests()

        print "requestIds:", requestIds, "type:", type(requestIds)

        invalidRequests = [r for r in requestIds if r not in spotRequests]
        if len(invalidRequests) > 0:
            return self.commonHeader() + markdown.markdown(
                "# ERROR\n\nRequests %s don't exist" % invalidRequests
                )

        self.eventLog.addLogMessage(
            self.getCurrentLogin(),
            "Cancelling spot requests: %s",
            requestIds
            )

        ec2.cancelSpotRequests(requestIds)
        raise cherrypy.HTTPRedirect("/spotRequests")

    @cherrypy.expose
    def setDefaultAvailabilityZone(self, az):
        if not self.isAuthenticated():
            return self.authenticate()

        self.testLooperMachines.availabilityZone = az

    @cherrypy.expose
    def addSpotRequests_v2(self, instanceType, maxPrice, quantity):
        if not self.isAuthenticated():
            return self.authenticate()

        az = self.testLooperMachines.availabilityZone
        try:
            maxPrice = float(maxPrice)
        except:
            return self.commonHeader() + markdown.markdown(
                "# ERROR\n\nInvalid max price"
                )

        ec2 = self.ec2Factory()

        ec2.requestLooperInstances(
            maxPrice,
            instance_type=instanceType,
            instance_count=int(quantity),
            launch_group=None,
            availability_zone=az
            )

        self.eventLog.addLogMessage(
            self.getCurrentLogin(),
            "Added %s spot requests for type %s and max price of %s",
            quantity,
            instanceType,
            maxPrice
            )

        return "Success!"

    @cherrypy.expose
    def addSpotRequests(self, instanceType, maxPrice, availabilityZone):
        if not self.isAuthenticated():
            return self.authenticate()

        logging.info(
            "Add spot request. Instance type: %s, max price: %s, az: %s",
            instanceType, maxPrice, availabilityZone
            )
        try:
            maxPrice = float(maxPrice)
        except:
            return self.commonHeader() + markdown.markdown(
                "# ERROR\n\nInvalid max price"
                )

        matchedTuples = [
            match for match in self.availableInstancesAndCoreCount()
            if match[0] == instanceType
            ]

        if len(matchedTuples) == 0:
            try:
                maxPrice = float(maxPrice)
            except:
                return self.commonHeader() + markdown.markdown(
                    "# ERROR\n\nInvalid instance type"
                    )

        coreCount = matchedTuples[0][1]
        ec2 = self.ec2Factory()
        provisioned = 0.0
        min_price = 0.0075 * coreCount
        while True:
            provisioned += 1
            bid = maxPrice / provisioned
            if bid < min_price:
                break
            ec2.requestLooperInstances(bid,
                                       instance_type=instanceType,
                                       availability_zone=availabilityZone)

        self.eventLog.addLogMessage(
            self.getCurrentLogin(),
            "Added %s spot requests for type %s and max price of %s",
            provisioned,
            instanceType,
            maxPrice
            )

        raise cherrypy.HTTPRedirect("/spotRequests")

    @cherrypy.expose
    def githubReceivedAPush(self):
        return self.webhook()


    @cherrypy.expose
    def webhook(self):
        if 'Content-Length' not in cherrypy.request.headers:
            raise cherrypy.HTTPError(400, "Missing Content-Length header")
        rawbody = cherrypy.request.body.read(int(cherrypy.request.headers['Content-Length']))

        event = self.src_ctrl.verify_webhook_request(cherrypy.request.headers,
                                                     rawbody)
        if not event:
            logging.error("Invalid webhook request")
            raise cherrypy.HTTPError(400, "Invalid webhook request")

        #don't block the webserver itself, so we can do this in a background thread
        refreshInBackgroundThread = threading.Thread(target=self.refreshTestManager)

        t0 = time.time()
        logging.info("refreshing TestLooperManager")
        refreshInBackgroundThread.start()
        logging.info("refreshing TestLooperManager took %s seconds", time.time() - t0)


    @cherrypy.expose
    def test_looper_webhook(self):
        if 'Content-Length' not in cherrypy.request.headers:
            raise cherrypy.HTTPError(400, "Missing Content-Length header")
        rawbody = cherrypy.request.body.read(int(cherrypy.request.headers['Content-Length']))

        event = Github.verify_webhook_request(cherrypy.request.headers,
                                              rawbody,
                                              self.test_looper_webhook_secret)
        if not event:
            logging.error("Invalid webhook request")
            raise cherrypy.HTTPError(400, "Invalid webhook request")

        if event['branch'] == self.test_looper_branch:
            logging.info("Own branch '%s' changed. rebooting", self.test_looper_branch)

            #don't block the webserver itself, so we can do this in a background thread
            killThread = threading.Thread(target=self.killProcessInOneSecond)

            logging.info("restarting TestLooperManager")
            killThread.start()



    @staticmethod
    def killProcessInOneSecond():
        time.sleep(1.0)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    def refreshTestManager(self):
        with self.testManager.lock:
            self.testManager.updateBranchesUnderTest()

    @staticmethod
    def getJsonPayload():
        cl = cherrypy.request.headers['Content-Length']
        rawbody = cherrypy.request.body.read(int(cl))
        body = simplejson.loads(rawbody)

        return body

    def start(self):
        config = {
            'global': {
                "engine.autoreload.on":False,
                'server.socket_host': '0.0.0.0',
                'server.socket_port': self.httpPort,
                'server.show.tracebacks': False,
                'request.show_tracebacks': False,
                'tools.sessions.on': True,
                }
            }

        cherrypy.config.update(config)
        logging.info("STARTING HTTP SERVER")

        current_dir = os.path.dirname(__file__)
        cherrypy.tree.mount(self, '/', {
            '/favicon.ico': {
                'tools.staticfile.on': True,
                'tools.staticfile.filename': os.path.join(current_dir,
                                                          'content',
                                                          'favicon.ico')
                },
            '/css': {
                'tools.staticdir.on': True,
                'tools.staticdir.dir': os.path.join(current_dir, 'css')
                }
            })

        cherrypy.engine.autoreload.on = False

        cherrypy.engine.signals.subscribe()

        cherrypy.engine.start()


    @staticmethod
    def stop():
        logging.info("Stopping cherrypy engine")
        cherrypy.engine.exit()
        logging.info("Cherrypy engine stopped")
