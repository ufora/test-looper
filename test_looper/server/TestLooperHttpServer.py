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
import urllib
import pytz
import simplejson
import os

import test_looper.core.source_control as Github
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.PerformanceDataset as PerformanceDataset

import traceback

time.tzset()

def joinLinks(linkList):
    res = ""

    for l in linkList:
        if res:
            res = res + ", "
        res = res + l

    return res


class TestLooperHttpServer(object):
    def __init__(self,
                 address,
                 testManager,
                 cloud_connection,
                 artifactStorage,
                 src_ctrl,
                 event_log,
                 auth_level,
                 httpPort,
                 enable_advanced_views,
                 wetty_port
                 ):
        """Initialize the TestLooperHttpServer

        testManager - a TestManager.TestManager object
        httpPortOverride - the port to listen on for http requests
        auth_level - none: no authentication at all
                     write: need authentication for "write" operations
                     full: must authenticate to access anything
        """
        self.address = address
        self.testManager = testManager
        self.cloud_connection = cloud_connection
        self.accessTokenHasPermission = {}
        self.httpPort = httpPort
        self.auth_level = auth_level
        self.src_ctrl = src_ctrl
        self.eventLog = event_log
        self.wetty_port = wetty_port
        self.eventLog.addLogMessage("test-looper", "TestLooper initialized")
        self.defaultCoreCount = 4
        self.enable_advanced_views = enable_advanced_views
        self.artifactStorage = artifactStorage

        self.refresh_lock = threading.Lock()
        self.need_refresh = False
        self.refresh_thread = None


    def addLogMessage(self, format_string, *args, **kwargs):
        self.eventLog.addLogMessage(self.getCurrentLogin(), format_string, *args, **kwargs)


    def getCurrentLogin(self):
        login = cherrypy.session.get('github_login', None)
        if login is None and self.is_authenticated():
            token = self.access_token()
            login = cherrypy.session['github_login'] = self.src_ctrl.getUserNameFromToken(token)
        return login or "Guest"


    def authenticate(self):
        auth_url = self.src_ctrl.authenticationUrl()

        if auth_url is not None:
            #stash the current url
            self.save_current_url()
            raise cherrypy.HTTPRedirect(auth_url)
        else:
            cherrypy.session['github_access_token'] = "DUMMY"


    def save_current_url(self):
        cherrypy.session['redirect_after_authentication'] = self.currentUrl()


    @staticmethod
    def is_authenticated():
        return 'github_access_token' in cherrypy.session


    @staticmethod
    def access_token():
        return cherrypy.session['github_access_token']


    def can_write(self):
        if self.auth_level == 'none':
            return True

        if not self.is_authenticated():
            return False

        token = self.access_token()
        is_authorized = self.accessTokenHasPermission.get(token)
        if is_authorized is None:
            is_authorized = self.src_ctrl.authorize_access_token(token)
            self.accessTokenHasPermission[token] = is_authorized

            self.addLogMessage(
                "Authorization: %s",
                "Granted" if is_authorized else "Denied"
                )
        return is_authorized


    def authorize(self, read_only):
        if self.auth_level == 'none' or (self.auth_level == 'write' and read_only):
            return

        if not self.is_authenticated():
            # this redirects to the login page. Authorization will take place
            # again once the user is redirected back to the app.
            self.authenticate()
        else:
            if self.can_write():
                return

            message = (
                "You are not authorized to access this repository" if read_only else
                "You are not authorized to perform the requested operation"
                )
            raise cherrypy.HTTPError(403, message)


    @cherrypy.expose
    def logout(self):
        token = cherrypy.session.pop('github_access_token', None)
        if token and token in self.accessTokenHasPermission:
            del self.accessTokenHasPermission[token]

        cherrypy.session.pop('github_login')

        raise cherrypy.HTTPRedirect(self.address + "/")


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

        print "redirecting to ", cherrypy.session.pop('redirect_after_authentication', None)

        raise cherrypy.HTTPRedirect(
            cherrypy.session.pop('redirect_after_authentication', None) or self.address + "/"
            )


    def errorPage(self, errorMessage):
        return self.commonHeader() + "\n" + markdown.markdown("#ERROR\n\n" + errorMessage)


    @cherrypy.expose
    def index(self):
        raise cherrypy.HTTPRedirect(self.address + "/branches")


    @cherrypy.expose
    def test(self, testId):
        self.authorize(read_only=True)

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
                internalIpAddress = test.machineToInternalIpMap[machine]
                row.append(internalIpAddress)

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
                ("<br>Branches: %s\n<br>" % 
                        (lambda x: x.render() if not isinstance(x,str) else x)(
                            joinLinks(self.branchLink(b.branchName) for b in commit.branches)
                            )
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
        self.authorize(read_only=True)
        return (
            self.commonHeader() +
            HtmlGeneration.grid(
                self.prioritizationGrid()
                )
            )

    @cherrypy.expose
    def test_contents(self, testId, key):
        return self.artifactStorage.testContentsHtml(testId, key)

    def testResultDownloadUrl(self, testId, key):
        return "/test_contents?testId=%s&key=%s" % (testId, key)

    def testResultKeys(self, testId):
        return self.artifactStorage.testResultKeysFor(testId)

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
                    str(not commit.isUnderTest),
                    joinLinks(self.branchLink(b.branchName) for b in commit.branches),
                    self.sourceLinkForCommit(commit)
                    ])

            return grid

    @staticmethod
    def commitLink(commit, failuresOnly=False, testName=None, length=7):
        if isinstance(commit, basestring):
            commitId = commit
            text = commit[:length]
        else:
            commitId = commit.commitId
            text = commit.subject if len(commit.subject) < 71 else commit.subject[:70] + '...'
        extras = {}

        if failuresOnly:
            extras["failuresOnly"] = 'true'
        if testName:
            extras["testName"] = testName

        return HtmlGeneration.link(
            text,
            "/commit/" + commitId + ("?" if extras else "") + urllib.urlencode(extras),
            hover_text=None if isinstance(commit, basestring) else commit.subject
            )

    @staticmethod
    def branchLink(branch):
        return HtmlGeneration.link(branch, "/branch?branchName=%s" % branch)


    def disable_if_cant_write(self, style):
        if self.can_write() or "disabled" in style:
            return style
        return style + " disabled"


    def small_clear_button(self, url, label=None):
        label = label or "clear"
        return HtmlGeneration.Link(url,
                                   label,
                                   is_button=True,
                                   button_style=self.disable_if_cant_write('btn-danger btn-xs'))


    def clearBranchLink(self, branch, redirect):
        return self.small_clear_button(
            "/clearBranch?" + urllib.urlencode({'branch':branch, 'redirect': redirect}),
            )

    def clearCommitIdLink(self, commitId, redirect):
        return self.small_clear_button(
            "/clearCommit?" + urllib.urlencode({'commitId': commitId, 'redirect': redirect}),
            )

    def sourceLinkForCommit(self, commit):
        url = self.src_ctrl.commit_url(commit.commitId)
        if url:
            return HtmlGeneration.link(commit.commitHash[:7], url)
        else:
            return HtmlGeneration.lightGrey(commit.commitHash[:7])


    @cherrypy.expose
    def clearCommit(self, commitId, redirect):
        self.authorize(read_only=False)

        with self.testManager.lock:
            self.testManager.clearCommitId(commitId)

        raise cherrypy.HTTPRedirect(redirect)

    @cherrypy.expose
    def clearBranch(self, branch, redirect=None):
        self.authorize(read_only=False)

        with self.testManager.lock:
            commits = self.testManager.branches[branch].commits

            for c in commits:
                self.testManager.clearCommitId(c)

        if redirect is not None:
            raise cherrypy.HTTPRedirect(redirect)

    @cherrypy.expose
    def machines(self):
        self.authorize(read_only=True)

        instancesByIp = {
            i.ip_address or i.private_ip_address: i
            for i in self.cloud_connection.getLooperInstances()
            }

        spotRequests = self.cloud_connection.getLooperSpotRequests()

        with self.testManager.lock:
            grid = [["MACHINE", "PING", "STATE", "TYPE", "SPOT REQ ID",
                     "SPOT REQUEST STATE", ""]]

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

                    row.append(instance.state)
                    row.append(instance.instance_type)
                    row.append(instance.spot_instance_request_id)

                    spot_request = spotRequests.get(instance.spot_instance_request_id)
                    row.append(spot_request.status.code if spot_request else '')

                    row.append(
                        self.small_clear_button(
                            "/terminateMachine?machineId=" + machineId,
                            "terminate"
                            )
                        )
                else:
                    row.append("")
                    row.append("<shut down>")

                grid.append(row)

            return self.commonHeader() + HtmlGeneration.grid(grid)

    @cherrypy.expose
    def terminateMachine(self, machineId):
        self.authorize(read_only=False)

        instancesByIp = {
            i.ip_address or i.private_ip_address: i
            for i in self.cloud_connection.getLooperInstances()
            }

        if machineId not in instancesByIp:
            return self.errorPage("Unknown machine %s" % machineId)

        instancesByIp[machineId].terminate()

        raise cherrypy.HTTPRedirect(self.address + "/machines")

    @cherrypy.expose
    def machine(self, machineId):
        self.authorize(read_only=True)

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



    @cherrypy.expose
    def commit(self, repoName, commitHash, failuresOnly=False, testName=None):
        commitId = repoName + "/" + commitHash

        self.authorize(read_only=True)

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

            grid = self.gridForTestList_(sortedTests, commit=commit, failuresOnly=failuresOnly)

            header = """## Commit %s: %s\n""" % (self.sourceLinkForCommit(commit).render(),
                                                 commit.subject)
            for b in commit.branches:
                header += """### Branch: %s\n""" % self.branchLink(b.branchName).render()

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

            header = self.commonHeader() + markdown.markdown(header)

            buttons = []
            try:
                defs = self.testManager.testDefinitionsForCommit(commitId)
            except:
                defs = None

            env_vals = defs.environments.values() if defs else []

            if env_vals:
                buttons.append(HtmlGeneration.makeHtmlElement(markdown.markdown("#### Environments")))
                for env in sorted(env_vals, key=lambda e: e.testName):
                    buttons.append(
                        HtmlGeneration.Link(self.bootTestOrEnvUrl(commitId, env.testName, env.portExpose),
                           env.testName,
                           is_button=True,
                           button_style=self.disable_if_cant_write('btn-danger btn-xs')
                           )
                        )
                    buttons.append(HtmlGeneration.makeHtmlElement("&nbsp;"*2))
                buttons.append(HtmlGeneration.makeHtmlElement("<br>"*2))

            test_vals = defs.tests.values() if defs else []
            if test_vals:
                buttons.append(HtmlGeneration.makeHtmlElement(markdown.markdown("#### Tests")))
                for test in sorted(test_vals, key=lambda e: e.testName):
                    buttons.append(
                        HtmlGeneration.Link(self.bootTestOrEnvUrl(commitId, test.testName, test.portExpose),
                           test.testName,
                           is_button=True,
                           button_style=self.disable_if_cant_write('btn-danger btn-xs')
                           )
                        )
                    buttons.append(HtmlGeneration.makeHtmlElement("&nbsp;"*2))
                buttons.append(HtmlGeneration.makeHtmlElement("<br>"*2))

            return header + HtmlGeneration.HtmlElements(buttons).render() + HtmlGeneration.grid(grid)

    def bootTestOrEnvUrl(self, commitId, testName, ports):
        addr = self.address
        items = addr.split(":")
        def isint(x):
            try:
                int(x)
                return True
            except:
                return False
        if isint(items[-1]):
            addr = ":".join(items[:-1])

        args = {'commit': commitId, 'test': testName}
        if ports:
            args["ports"] = ports
        return addr + ":" + str(self.wetty_port) + "/wetty?" + urllib.urlencode(args)


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


    def login_link(self):
        self.save_current_url()
        return '<a href="%s">Login</a>' % self.src_ctrl.authenticationUrl()


    def logout_link(self):
        return ('<a href="/logout">'
                'Logout [%s] <span class="glyphicon glyphicon-user" aria-hidden="true"/>'
                '</a>') % self.getCurrentLogin()


    def commonHeader(self):
        headers = []
        headers.append(
            '<div align="right"><h5>%s</h5></div>' % (
                self.logout_link() if self.is_authenticated() else self.login_link())
            )

        nav_links = [
            ('Branches', '/branches')
            ]

        if self.cloud_connection.isSpotEnabled():
            nav_links += [
                ('Spot Requests', '/spotRequests'),
                ]
            nav_links += [
                    ('Workers', '/machines')
                    ]

        if self.enable_advanced_views:
            nav_links += [
                ('Activity Log', '/eventLogs'),
                ('Test Queue', '/testPrioritization')
                ]
        
        headers += ['<ul class="nav nav-pills">'] + [
            '<li role="presentation" class="{is_active}"><a href="{link}">{label}</a></li>'.format(
                is_active="active" if link == cherrypy.request.path_info else "",
                link=link,
                label=label)
            for label, link in nav_links
            ] + ['</ul>']
        return HtmlGeneration.headers + "\n" + "\n".join(headers)


    def toggleBranchUnderTestLink(self, branch):
        icon = "glyphicon-pause" if branch.isUnderTest else "glyphicon-play"
        hover_text = "%s testing this branch" % ("Pause" if branch.isUnderTest else "Start")
        button_style = "btn-xs " + ("btn-success active" if branch.isUnderTest else "btn-default")
        return HtmlGeneration.Link(
            "/toggleBranchUnderTest?branchName=" + branch.branchName,
            '<span class="glyphicon %s" aria-hidden="true"></span>' % icon,
            is_button=True,
            button_style=self.disable_if_cant_write(button_style),
            hover_text=hover_text
            )


    @cherrypy.expose
    def toggleBranchUnderTest(self, branchName):
        self.authorize(read_only=False)

        with self.testManager.lock:
            branch = self.testManager.branches[branchName]
            branch.setIsUnderTest(not branch.isUnderTest)

        raise cherrypy.HTTPRedirect(self.address + "/branches")

    @cherrypy.expose
    def refresh(self):
        self.refreshBranches(block=True)
        raise cherrypy.HTTPRedirect(self.address + "/branches")

    @cherrypy.expose
    def refreshNonblocking(self):
        self.refreshBranches(block=False)

    def refreshBranches(self, block=True):
        with self.refresh_lock:
            self.need_refresh = True
            if self.refresh_thread is None:
                self.refresh_thread = threading.Thread(target=self.refreshTestManager)
                self.refresh_thread.start()
            refresh_thread = self.refresh_thread

        if block:
            refresh_thread.join()


    def branchesGrid(self):
        t0 = time.time()
        with self.testManager.lock:
            lock_time = time.time()
            branches = self.testManager.distinctBranches()
            branch_list_time = time.time()

            refresh_button = HtmlGeneration.Link(
                "/refresh",
                '<span class="glyphicon glyphicon-refresh " aria-hidden="true" />',
                is_button=True,
                button_style='btn-default btn-xs',
                hover_text='Refresh branches'
                )

            grid = [["TEST", "BRANCH NAME", "COMMIT COUNT", "RUNNING",
                     "FULL TEST PASSES", "TOTAL TESTS", refresh_button]]

            for b in sorted(branches):
                branch = self.testManager.branches[b]
                commits = branch.commits.values()

                row = []
                row.append(self.toggleBranchUnderTestLink(branch))
                row.append(HtmlGeneration.link(b, "/branch?branchName=" + b))
                row.append(str(len(commits)))

                if commits:
                    row.append(str(sum([c.totalRunningCount() for c in commits])))

                    passes = sum([c.fullPassesCompleted() for c in commits])

                    totalRuns = sum([c.totalCompletedTestRuns() for c in commits])

                    row.append(passes)
                    row.append(str(totalRuns))
                    row.append(self.clearBranchLink(b, "/branches"))

                grid.append(row)

            end_time = time.time()
            logging.info("branches page timing - Total: %.2f, lock: %.2f, "
                         "branch_list: %.2f, grid: %.2f",
                         end_time - t0,
                         lock_time - t0,
                         branch_list_time - lock_time,
                         end_time - branch_list_time)

            return grid

    @cherrypy.expose
    def branches(self):
        self.authorize(read_only=True)

        grid = HtmlGeneration.grid(self.branchesGrid())
        grid += HtmlGeneration.Link("/disableAllTargetedTests",
                                    "Stop all drilling",
                                    is_button=True,
                                    button_style=self.disable_if_cant_write("btn-default")).render()

        return self.commonHeader() + grid


    @cherrypy.expose
    def disableAllTargetedTests(self):
        self.authorize(read_only=False)

        with self.testManager.lock:
            for branch in self.testManager.branches.itervalues():
                branch.setTargetedTestList([])
                branch.setTargetedCommitIds([])

        raise cherrypy.HTTPRedirect(self.address + "/branches")


    @cherrypy.expose
    def branchPerformance(self, branchName, prefix=""):
        self.authorize(read_only=True)

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

    def toggleBranchTargetedTestListLink(self, branch, testType, testGroupsToExpand):
        is_drilling = testType in branch.targetedTestList()
        icon = "glyphicon-minus" if is_drilling else "glyphicon-plus"
        hover_text = "Run less of this test" if is_drilling else "Run more of this test"
        button_style = "btn-default btn-xs" + (" active" if is_drilling else "")
        return HtmlGeneration.Link(
            "/toggleBranchTestTargeting?branchName=%s&testType=%s&testGroupsToExpand=%s" % (
                branch.branchName, testType, testGroupsToExpand
                ),
            '<span class="glyphicon %s" aria-hidden="true"></span>' % icon,
            is_button=True,
            button_style=self.disable_if_cant_write(button_style),
            hover_text=hover_text
            )

    def toggleBranchTargetedCommitIdLink(self, branch, commitId):
        is_drilling = commitId in branch.targetedCommitIds()
        icon = "glyphicon-minus" if is_drilling else "glyphicon-plus"
        hover_text = "Run less of this commit" if is_drilling else "Run more of this commit"
        button_style = "btn-default btn-xs" + (" active" if is_drilling else "")
        return HtmlGeneration.Link(
                "/toggleBranchCommitTargeting?branchName=%s&commitId=%s" % (
                    branch.branchName, commitId
                    ),
                '<span class="glyphicon %s" aria-hidden="true"></span>' % icon,
                is_button=True,
                button_style=self.disable_if_cant_write(button_style),
                hover_text=hover_text
                )

    @cherrypy.expose
    def toggleBranchTestTargeting(self, branchName, testType, testGroupsToExpand):
        self.authorize(read_only=False)

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
            self.address + "/branch?branchName=%s&testGroupsToExpand=%s" % (branchName, testGroupsToExpand)
            )


    @cherrypy.expose
    def toggleBranchCommitTargeting(self, branchName, commitId):
        self.authorize(read_only=False)
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

        raise cherrypy.HTTPRedirect(self.address + "/branch?branchName=" + branchName)

    @staticmethod
    def errRateVal(testCount, successCount):
        if testCount == 0:
            return 0

        successCount = float(successCount)

        toReturn = 1.0 - successCount / testCount
        return toReturn


    @cherrypy.expose
    def branch(self, branchName, testGroupsToExpand=None, perfprefix=None):
        self.authorize(read_only=True)

        t0 = time.time()
        with self.testManager.lock:
            lock_time = time.time()
            if branchName not in self.testManager.branches:
                return self.errorPage("Branch %s doesn't exist" % branchName)

            branch = self.testManager.branches[branchName]

            return self.testPageForCommits(branch.commitsInOrder, "Branch `" + branchName + "`", testGroupsToExpand, branch)

    @cherrypy.expose
    def revlist(self, repoName, revlist):
        self.authorize(read_only=True)

        t0 = time.time()
        with self.testManager.lock:
            lock_time = time.time()
            
            repo = self.testManager.source_control.getRepo(repoName)

            commitHashesParentsAndTitles = repo.source_repo.commitsInRevList(revlist)

            commitHashes = set([c[0] for c in commitHashesParentsAndTitles])
            
            commits = {}
            for commitHash, parentHashes, commitTitle in commitHashesParentsAndTitles:
                commitId = repoName+"/"+commitHash

                commits[commitId] = self.testManager.createCommit(commitId,
                                                                  parentHashes,
                                                                  commitTitle
                                                                  )

            commitsByHash = {c.commitHash: c for c in commits.values()}

            commitsInOrder = [commitsByHash[hash] for hash, _, _ in commitHashesParentsAndTitles]

            return self.testPageForCommits(commitsInOrder, "Revlist `" + revlist + "`", None, None)

    def testPageForCommits(self, commits, headerText, testGroupsToExpand, branch):
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

        commit_string = ""
        detail_divs = ""

        ids_to_resize = []

        branches = {}

        commit_hashes = {c.commitHash: c for c in commits}
        children = {c.commitHash: [] for c in commits}
        parents = {}

        for c in commits:
            parents[c.commitHash] = [p for p in c.parentHashes if p in commit_hashes]
            for p in parents[c.commitHash]:
                children[p].append(c.commitHash)
        
        for c in commits:
            if not parents[c.commitHash]:
                branchname = "branch_%s" % len(branches)

                commit_string += 'var %s = gitgraph.branch("%s");\n' % (branchname, branchname)
                branches[c.commitHash] = branchname

        for commit_ix, c in enumerate(reversed(commits)):
            commit_string +=  "//%s -- %s\n" % (commit_ix, c.commitHash)

            parentsWeHave = parents[c.commitHash]

            if len(parentsWeHave) == 0:
                #push a commit onto the branch
                our_branch = branches[c.commitHash]

                commit_string += "%s.commit({sha1: '%s', message: '%s', detailId: 'commit_%s'});\n" % (
                    branches[c.commitHash],
                    c.commitHash, 
                    c.subject.replace("'", "\\'"),
                    c.commitHash
                    )

            elif len(parentsWeHave) == 1:
                #push a commit onto the branch
                our_branch = branches[(parentsWeHave[0], c.commitHash)]

                commit_string += "%s.commit({sha1: '%s', message: '%s', detailId: 'commit_%s'});\n" % (
                    our_branch,
                    c.commitHash, 
                    c.subject.replace("'", "\\'"),
                    c.commitHash
                    )
            else:
                our_branch = branches[(parentsWeHave[0], c.commitHash)]
                other_branch = branches[(parentsWeHave[1], c.commitHash)]

                commit_string += "%s.merge(%s, {sha1: '%s', message: '%s', detailId: 'commit_%s'}).delete();" % (other_branch, our_branch, 
                    c.commitHash, 
                    c.subject.replace("'", "\\'"),
                    c.commitHash
                    )

            if len(children[c.commitHash]) == 0:
                #nothing to do - this is terminal
                pass
            elif len(children[c.commitHash]) == 1:
                #one child gets to use this branch
                branches[(c.commitHash, children[c.commitHash][0])] = our_branch
            else:
                #this is a fork - one child gets to use the branch, and everyone else needs to get a fork
                branches[(c.commitHash, children[c.commitHash][0])] = our_branch
                for other_child in children[c.commitHash][1:]:
                    branchname = "branch_%s" % len(branches)

                    commit_string += 'var %s = %s.branch("%s");\n' % (branchname, our_branch, branchname)

                    branches[(c.commitHash, other_child)] = branchname



        for c in commits:
            gridrow = self.getBranchCommitRow(branch,
                                          c,
                                          testGroups,
                                          ungroupedUniqueTestIds,
                                          testGroupsToTests)

            grid.append(gridrow)

        header = (
            markdown.markdown("# " + headerText) + "\n\n" +
            '<p>Click the <span class="glyphicon glyphicon-plus" aria-hidden="true"></span>/'
            '<span class="glyphicon glyphicon-minus" aria-hidden="true"></span> buttons '
            'to increase/decrease the amount of testing on a given commit or test suite. '
            'If both a test suite and a commit are selected within a branch'
            ", only the cross section will received extra test coverage.</p><br>"
            )
        
        grid = HtmlGeneration.grid(grid, header_rows=2, rowHeightOverride=33)
        
        canvas = HtmlGeneration.gitgraph_canvas_setup(commit_string, grid)

        return self.commonHeader() + header + detail_divs + canvas


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
                    self.commitLink(commit.commitId)
                    ])
                )
            commitIndices[commit.commitId] = len(commitIndices)
            commit_perf_summary = commit.summarizePerfResults(prefix)
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
                    qs="&".join("%s=%s" % (k, v) for k, v in query_string.iteritems()),
                    relative=False
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
                if branch:
                    testHeaders.append(
                        self.toggleBranchTargetedTestListLink(branch,
                                                              testGroup,
                                                              ",".join(testGroupsToExpand))
                        )
                else:
                    testHeaders.append("")

                if branch:
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
                    testGroupExpandLinks.append(testGroup)
            else:
                testHeaders.append("")
                if branch:
                    testGroupExpandLinks.append(
                        HtmlGeneration.link(
                            testGroup,
                            "/branch?branchName=%s&testGroupsToExpand=%s" % (
                                branch.branchName,
                                ",".join(testGroupsToExpand + [testGroup])
                                )
                            )
                        )
                else:
                    testGroupExpandLinks.append(testGroup)
                
            testGroupExpandLinks[-1] = HtmlGeneration.pad(testGroupExpandLinks[-1], 20)
            testHeaders[-1] = HtmlGeneration.pad(testHeaders[-1], 20)

        grid = [["", "", "", ""] + testHeaders + ["", "", ""]]
        grid.append(
            ["COMMIT", "", "(running)", "FAIL RATE" + HtmlGeneration.whitespace*4] + \
            testGroupExpandLinks + \
            ["SOURCE", "", "branch"]
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

        row = [self.commitLink(commit)]

        if branch:
            row.append(self.toggleBranchTargetedCommitIdLink(branch, commit.commitId))

        row.append(
            commit.totalRunningCount() if commit.totalRunningCount() != 0 else ""
            )
        passRate = commit.passRate()
        row.append(self.errRate(1.0 - passRate) if passRate is not None else '')

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
                errRate = self.errRateAndTestCount(
                    stat.passCount + stat.failCount,
                    stat.passCount
                    )

                #check if this point in the commit-sequence has a statistically different
                #probability of failure from its peers and mark it if so.

                if branch:
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
                    row.append(HtmlGeneration.lightGrey(errRate))

            if testGroup in ungroupedUniqueTestIds:
                if commit.isTargetedTest(testGroup):
                    row[-1] = HtmlGeneration.blueBacking(row[-1])
            else:
                if allTestsInGroupAreTargetedInCommit(commit, testGroup):
                    row[-1] = HtmlGeneration.blueBacking(row[-1])

                if anyTestInGroupIsTargetedInCommit(commit, testGroup):
                    row[-1] = HtmlGeneration.lightGreyBacking(row[-1])

        row.append(self.sourceLinkForCommit(commit))
        
        row.append(
            HtmlGeneration.lightGrey("invalid test file") 
                    if commit.testScriptDefinitionsError is not None
            else HtmlGeneration.lightGrey("no tests") 
                    if len(commit.testScriptDefinitions) == 0
            else self.clearCommitIdLink(commit.commitId, "/branch?branchName=" + branch.branchName)
                    if branch
            else ""
            )

        row.append(joinLinks(self.branchLink(b.branchName) for b in commit.branches))
        return row

    @staticmethod
    def errRateAndTestCount(testCount, successCount):
        if testCount == 0:
            return "  0     "

        successCount = float(successCount)

        errRate = 1.0 - successCount / testCount

        if errRate == 0.0:
            return "%4s@%3s%s" % (testCount, 0, "%")

        if errRate < 0.01:
            errRate *= 10000
            errText = '.%2s' % int(errRate)
        elif errRate < 0.1:
            errRate *= 100
            errText = '%s.%s' % (int(errRate), int(errRate * 10) % 10)
        else:
            errRate *= 100
            errText = '%3s' % int(errRate)

        return "%4s@%3s" % (testCount, errText) + "%"


    @staticmethod
    def errRate(frac):
        tr = "%.1f" % (frac * 100) + "%"
        tr = tr.rjust(6)

        if frac < .1:
            tr = HtmlGeneration.lightGrey(tr)

        if frac > .9:
            tr = HtmlGeneration.red(tr)

        return tr


    @cherrypy.expose
    def eventLogs(self):
        self.authorize(read_only=True)
        return self.commonHeader() + self.generateEventLogHtml(1000)


    def getCurrentSpotRequestGrid(self):
        spotRequests = sorted(
            self.cloud_connection.getLooperSpotRequests().itervalues(),
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
                HtmlGeneration.Link(
                    "/cancelSpotRequests?" + urllib.urlencode(
                        {'requestIds': ",".join([str(r.id) for r in spotRequests])}
                        ),
                    "cancel",
                    is_button=True,
                    button_style=self.disable_if_cant_write("btn-danger btn-xs")
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

        if len(grid) == 1:
            grid.append(["No open spot instance requests"])
        return grid


    def get_spot_prices(self):
        prices = {}
        for instance_type, _ in self.cloud_connection :
            prices_by_zone = self.cloud_connection.currentSpotPrices(instanceType=instance_type)
            prices[instance_type] = {
                zone: price for zone, price in sorted(prices_by_zone.iteritems())
                }
        return prices

    def getSpotInstancePriceGrid(self, prices):
        availability_zones = sorted(prices.itervalues().next().keys())
        grid = [["Instance Type"] + availability_zones]
        for instance_type, _ in self.cloud_connection.available_instance_types_and_core_count:
            grid.append([instance_type] + ["$%s" % prices[instance_type][az]
                                           for az in sorted(prices[instance_type].keys())])

        return grid


    def getAddSpotRequestForm(self, availability_zones):
        instanceTypeDropDown = HtmlGeneration.selectBox(
            'instanceType',
            [
                (instance_type, "%s cores (%s)" % (core_count, instance_type))
                for instance_type, core_count in
                self.cloud_connection.available_instance_types_and_core_count
            ],
            self.defaultCoreCount)
        availabilityZoneDropDown = HtmlGeneration.selectBox(
            'availabilityZone',
            sorted([(az, az) for az in availability_zones]),
            '')

        addForm = """
            <h2>Add instances:</h2>
            <form action="/addSpotRequests" method="post" class="form-inline">
              <div class="form-group">
                <label for="instanceType">Type</label>
                %s
              </div>
              <div class="form-group">
                <label for="maxPrice">Max price</label>
                <input type="text" name="maxPrice" class="form-control">
              </div>
              <div class="form-group">
                <label for="availbilityZone">Availability zone</label>
                %s
              </div>
              <button type="submit" value="Add" class="btn btn-primary">Add</button>
            </form>
            """ % (instanceTypeDropDown, availabilityZoneDropDown)
        return addForm

    @cherrypy.expose
    def spotRequests(self):
        self.authorize(read_only=True)

        spot_prices = self.get_spot_prices()

        grid = self.getCurrentSpotRequestGrid()
        has_open_requests = len(grid) > 1 and len(grid[1]) > 1

        button_style = "btn-danger" + ("" if has_open_requests else " disabled")
        clearAll = HtmlGeneration.Link(
            "/cancelAllSpotRequests",
            "Cancel all requests",
            is_button=True,
            button_style=self.disable_if_cant_write(button_style)
            ).render()

        availability_zones = spot_prices.itervalues().next().keys()
        addForm = (self.getAddSpotRequestForm(availability_zones) if self.can_write() else '')

        spotPrices = self.getSpotInstancePriceGrid(spot_prices)

        return HtmlGeneration.stack(
            self.commonHeader(),
            HtmlGeneration.grid(grid),
            clearAll,
            addForm,
            "<br/>"*2,
            markdown.markdown("## Spot Instance Prices\n"),
            HtmlGeneration.grid(spotPrices),
            "<br>"*2 + self.generateEventLogHtml()
            )

    def generateEventLogHtml(self, maxMessageCount=10):
        messages = self.eventLog.getTopNLogMessages(maxMessageCount)

        return markdown.markdown("## Most recent actions:\n\n") + HtmlGeneration.grid(
            [["Date", "user", "Action"]] +
            [[msg["date"], msg["user"], msg["message"]] for msg in reversed(messages)]
            )


    @cherrypy.expose
    def cancelAllSpotRequests(self, instanceType=None):
        self.authorize(read_only=False)

        self.cloud_connection = self.cloud_connection
        spotRequests = self.cloud_connection.getLooperSpotRequests()
        if instanceType is not None:
            spotRequests = {
                k: v for k, v in spotRequests.iteritems() \
                    if v.launch_specification.instance_type == instanceType
                }

        self.cloud_connection.cancelSpotRequests(spotRequests.keys())

        self.addLogMessage("Canceled all spot requests.")

        raise cherrypy.HTTPRedirect(self.address + "/spotRequests")

    @cherrypy.expose
    def cancelSpotRequests(self, requestIds):
        self.authorize(read_only=False)
        requestIds = requestIds.split(',')

        spotRequests = self.cloud_connection.getLooperSpotRequests()

        print "requestIds:", requestIds, "type:", type(requestIds)

        invalidRequests = [r for r in requestIds if r not in spotRequests]
        if len(invalidRequests) > 0:
            return self.commonHeader() + markdown.markdown(
                "# ERROR\n\nRequests %s don't exist" % invalidRequests
                )

        self.addLogMessage("Cancelling spot requests: %s", requestIds)

        self.cloud_connection.cancelSpotRequests(requestIds)
        raise cherrypy.HTTPRedirect(self.address + "/spotRequests")


    @cherrypy.expose
    def addSpotRequests(self, instanceType, maxPrice, availabilityZone):
        self.authorize(read_only=False)

        logging.info(
            "Add spot request. Instance type: %s, max price: %s, az: %s",
            instanceType, maxPrice, availabilityZone
            )
        try:
            maxPrice = float(maxPrice)
        except ValueError:
            return self.commonHeader() + markdown.markdown(
                "# ERROR\n\nInvalid max price"
                )

        coreCount = [c for i, c in self.cloud_connection.available_instance_types_and_core_count
                     if i == instanceType]
        if not coreCount:
            return self.commonHeader() + markdown.markdown(
                "# ERROR\n\nInvalid instance type"
                )

        provisioned = 0.0
        min_price = 0.0075 * coreCount[0]
        while True:
            provisioned += 1
            bid = maxPrice / provisioned
            if bid < min_price:
                break
            self.cloud_connection.requestLooperInstances(bid,
                                       instance_type=instanceType,
                                       availability_zone=availabilityZone)

        self.addLogMessage("Added %s spot requests for type %s and max price of %s",
                           provisioned,
                           instanceType,
                           maxPrice)

        raise cherrypy.HTTPRedirect(self.address + "/spotRequests")

    @cherrypy.expose
    def githubReceivedAPush(self):
        return self.webhook()

    @cherrypy.expose
    def webhook(self, *args, **kwds):
        if 'Content-Length' not in cherrypy.request.headers:
            raise cherrypy.HTTPError(400, "Missing Content-Length header")

        if cherrypy.request.headers['Content-Type'] == "application/x-www-form-urlencoded":
            payload = simplejson.loads(cherrypy.request.body_params['payload'])
        else:
            payload = simplejson.loads(cherrypy.request.body.read(int(cherrypy.request.headers['Content-Length'])))

        event = self.src_ctrl.verify_webhook_request(cherrypy.request.headers, payload)

        if not event:
            logging.error("Invalid webhook request")
            raise cherrypy.HTTPError(400, "Invalid webhook request")

        #don't block the webserver itself, so we can do this in a background thread
        self.refreshBranches(block=False)

    def refreshTestManager(self):
        need_refresh = True
        while need_refresh:
            with self.refresh_lock:
                self.need_refresh = False

            with self.testManager.lock:
                self.testManager.updateBranchesUnderTest()

            with self.refresh_lock:
                need_refresh = self.need_refresh

        with self.refresh_lock:
            self.refresh_thread = None


    def start(self):
        config = {
            'global': {
                "engine.autoreload.on":False,
                'server.socket_host': '0.0.0.0',
                'server.socket_port': self.httpPort,
                'server.show_tracebacks': False,
                'request.show_tracebacks': False,
                'tools.sessions.on': True,
                }
            }
        print "server is ", cherrypy.server
        cherrypy.config.update(config)
        print "server is now ", cherrypy.server

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
                },
            '/js': {
                'tools.staticdir.on': True,
                'tools.staticdir.dir': os.path.join(current_dir, 'content', 'js')
                }
            })

        cherrypy.server.socket_port = self.httpPort

        cherrypy.engine.autoreload.on = False

        cherrypy.engine.signals.subscribe()

        print "***************"
        print "config = ", cherrypy.config
        print "server = ", cherrypy.server
        print "engine = ", cherrypy.engine

        cherrypy.engine.start()


    @staticmethod
    def stop():
        logging.info("Stopping cherrypy engine")
        cherrypy.engine.exit()
        logging.info("Cherrypy engine stopped")
