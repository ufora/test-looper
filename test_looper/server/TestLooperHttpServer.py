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

        raise cherrypy.HTTPRedirect(
            cherrypy.session.pop('redirect_after_authentication', None) or self.address + "/"
            )


    def errorPage(self, errorMessage):
        return self.commonHeader() + "\n" + markdown.markdown("#ERROR\n\n" + errorMessage)


    @cherrypy.expose
    def index(self):
        raise cherrypy.HTTPRedirect(self.address + "/repos")


    @cherrypy.expose
    def test(self, testId):
        self.authorize(read_only=True)

        with self.testManager.database.view():
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

            commit = test.commit
            return (
                self.commonHeader() +
                markdown.markdown("# Test\n") +
                markdown.markdown("Test: %s\n" % testId) +
                ("<br>Branches: %s\n<br>" % 
                        (lambda x: x.render() if not isinstance(x,str) else x)(
                            joinLinks(self.branchLink(b) for b in commit.branches)
                            )
                ) +
                markdown.markdown("## Artifacts\n") +
                HtmlGeneration.grid(grid) + (
                    "<br>" * 3 + markdown.markdown("## Machine Assignments\n") +
                    HtmlGeneration.grid(machinesGrid)
                    )
                )

    @cherrypy.expose
    def test_contents(self, testId, key):
        return self.artifactStorage.testContentsHtml(testId, key)

    def testResultDownloadUrl(self, testId, key):
        return "/test_contents?testId=%s&key=%s" % (testId, key)

    def testResultKeys(self, testId):
        return self.artifactStorage.testResultKeysFor(testId)

    @staticmethod
    def commitLink(commit, failuresOnly=False, testName=None, length=7):
        commitId = commit.repo.name + "/" + commit.hash

        subject = "<not loaded yet>" if not commit.data else commit.data.subject

        text = subject if len(subject) < 71 else subject[:70] + '...'
        
        extras = {}

        if failuresOnly:
            extras["failuresOnly"] = 'true'
        if testName:
            extras["testName"] = testName

        return HtmlGeneration.link(
            text,
            "/commit/" + commitId + ("?" if extras else "") + urllib.urlencode(extras),
            hover_text=None if isinstance(commit, basestring) else subject
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
                                   button_style=self.disable_if_cant_write('btn-danger btn-xs'))


    def clearBranchLink(self, branch):
        return self.small_clear_button(
            "/clearBranch?" + urllib.urlencode({'reponame': branch.repo.name, 'branchname':branch.branchname, 'redirect': self.redirect()}),
            )

    def clearCommitIdLink(self, commitId):
        return self.small_clear_button(
            "/clearCommit?" + urllib.urlencode({'commitId': commitId, 'redirect': self.redirect()}),
            )

    def sourceLinkForCommit(self, commit):
        url = self.src_ctrl.commit_url(commit.repo.name + "/" + commit.hash)
        if url:
            return HtmlGeneration.link(commit.hash[:7], url)
        else:
            return HtmlGeneration.lightGrey(commit.hash[:7])


    @cherrypy.expose
    def clearCommit(self, commitId, redirect):
        self.authorize(read_only=False)

        with self.testManager.database.view():
            self.testManager.clearCommitId(commitId)

        raise cherrypy.HTTPRedirect(redirect)

    @cherrypy.expose
    def clearBranch(self, branch, redirect=None):
        self.authorize(read_only=False)

        with self.testManager.database.view():
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

        with self.testManager.database.view():
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

        with self.testManager.database.view():
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

        with self.testManager.database.view():
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

            header = """## Commit `%s`: `%s`\n""" % (commit.commitHash[:10], commit.subject)
            for b in commit.branches:
                header += """### Branch: %s\n""" % self.branchLink(b).render()

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
            ('Repos', '/repos')
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


    def toggleBranchUnderTestLink(self, branch):
        icon = "glyphicon-pause" if branch.isUnderTest else "glyphicon-play"
        hover_text = "%s testing this branch" % ("Pause" if branch.isUnderTest else "Start")
        button_style = "btn-xs " + ("btn-success active" if branch.isUnderTest else "btn-default")
        
        return HtmlGeneration.Link(
            "/toggleBranchUnderTest?" + 
                urllib.urlencode({'repo': branch.repo.name, 'branchname':branch.branchname, 'redirect': self.redirect()}),
            '<span class="glyphicon %s" aria-hidden="true"></span>' % icon,
            is_button=True,
            button_style=self.disable_if_cant_write(button_style),
            hover_text=hover_text
            )


    @cherrypy.expose
    def toggleBranchUnderTest(self, repo, branchname, redirect):
        self.authorize(read_only=False)

        with self.testManager.database.transaction():
            branch = self.testManager.database.Branch.lookupOne(reponame_and_branchname=(repo, branchname))
            self.testManager.toggleBranchUnderTest(branch)

        raise cherrypy.HTTPRedirect(redirect)

    @cherrypy.expose
    def refresh(self, redirect=None):
        self.refreshBranches(block=True)
        raise cherrypy.HTTPRedirect(redirect or self.address + "/repos")

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

    def redirect(self):
        qs = cherrypy.request.query_string

        return cherrypy.request.path_info + ("?" if qs else "") + qs

    def branchesGrid(self, repoName):
        t0 = time.time()
        with self.testManager.database.view():
            lock_time = time.time()
            repo = self.testManager.database.Repo.lookupOne(name=repoName)

            branches = self.testManager.database.Branch.lookupAll(repo=repo)
            
            refresh_button = HtmlGeneration.Link(
                "/refresh?" + urllib.urlencode({"redirect": self.redirect()}),
                '<span class="glyphicon glyphicon-refresh " aria-hidden="true" />',
                is_button=True,
                button_style='btn-default btn-xs',
                hover_text='Refresh branches'
                )

            grid = [["TEST", "BRANCH NAME", "COMMIT COUNT", refresh_button]]

            for branch in sorted(branches, key=lambda b:b.branchname):
                commits = self.testManager.commitsToDisplayForBranch(branch)

                row = []
                row.append(self.toggleBranchUnderTestLink(branch))
                row.append(self.branchLink(branch))
                row.append(str(len(commits)))

                if commits:
                    row.append(self.clearBranchLink(branch))

                grid.append(row)

            return grid

    @cherrypy.expose
    def repos(self):
        self.authorize(read_only=True)

        grid = HtmlGeneration.grid(self.reposGrid())
        
        return self.commonHeader() + grid

    def reposGrid(self):
        with self.testManager.database.view():
            repos = self.testManager.database.Repo.lookupAll(isActive=True)
            repoNames = [r.name for r in repos]

            grid = [["REPO NAME", "BRANCH COUNT"]]

            for r in sorted(repoNames):
                branches = self.testManager.database.Branch.lookupAll(
                    repo=self.testManager.database.Repo.lookupOne(name=r)
                    )

                grid.append([
                    HtmlGeneration.link(r, "/branches?repoName=" + r),
                    str(len(branches))
                    ])

            return grid

    @cherrypy.expose
    def branches(self, repoName):
        self.authorize(read_only=True)

        grid = HtmlGeneration.grid(self.branchesGrid(repoName))
        grid += HtmlGeneration.Link("/disableAllTargetedTests?" + 
                                        urllib.urlencode({'redirect': self.redirect()}),
                                    "Stop all drilling",
                                    is_button=True,
                                    button_style=self.disable_if_cant_write("btn-default")).render()

        return self.commonHeader() + grid


    @cherrypy.expose
    def disableAllTargetedTests(self, redirect):
        self.authorize(read_only=False)

        with self.testManager.database.view():
            for branch in self.testManager.branches.itervalues():
                branch.setTargetedTestList([])
                branch.setTargetedCommitIds([])

        raise cherrypy.HTTPRedirect(redirect)

    def toggleBranchTargetedTestListLink(self, branch, testType, testGroupsToExpand):
        is_drilling = False #testType in branch.targetedTestList()
        icon = "glyphicon-minus" if is_drilling else "glyphicon-plus"
        hover_text = "Run less of this test" if is_drilling else "Run more of this test"
        button_style = "btn-default btn-xs" + (" active" if is_drilling else "")
        return HtmlGeneration.Link(
            "/toggleBranchTestTargeting?" + urllib.urlencode({
                    "repo": branch.repo.name, 
                    "branchname": branch.branchname,
                    "testType": testType,
                    "testGroupsToExpand": ",".join(testGroupsToExpand)
                }),
            '<span class="glyphicon %s" aria-hidden="true"></span>' % icon,
            is_button=True,
            button_style=self.disable_if_cant_write(button_style),
            hover_text=hover_text
            )

    def toggleBranchTargetedCommitIdLink(self, branch, commit):
        is_drilling = False

        icon = "glyphicon-minus" if is_drilling else "glyphicon-plus"
        hover_text = "Run less of this commit" if is_drilling else "Run more of this commit"
        button_style = "btn-default btn-xs" + (" active" if is_drilling else "")
        return HtmlGeneration.Link(
                "/toggleBranchCommitTargeting?" + urllib.urlencode({
                    "repo": branch.repo.name, 
                    "branchname": branch.branchname,
                    "commitHash": commit.hash
                }),
                '<span class="glyphicon %s" aria-hidden="true"></span>' % icon,
                is_button=True,
                button_style=self.disable_if_cant_write(button_style),
                hover_text=hover_text
                )

    @cherrypy.expose
    def toggleBranchTestTargeting(self, reponame, branchname, testType, testGroupsToExpand):
        self.authorize(read_only=False)

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


    @cherrypy.expose
    def toggleBranchCommitTargeting(self, reponame, branchname, hash):
        self.authorize(read_only=False)

        with self.testManager.database.view():
            branch = self.testManager.database.Branch.lookupOne(reponame_and_branchname=(reponame, branchname))

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

        raise cherrypy.HTTPRedirect(self.branchUrl(branch))

    @staticmethod
    def errRateVal(testCount, successCount):
        if testCount == 0:
            return 0

        successCount = float(successCount)

        toReturn = 1.0 - successCount / testCount
        return toReturn


    @cherrypy.expose
    def branch(self, reponame, branchname, testGroupsToExpand=None):
        self.authorize(read_only=True)

        t0 = time.time()
        with self.testManager.database.view():
            branch = self.testManager.database.Branch.lookupAny(reponame_and_branchname=(reponame,branchname))

            if branch is None:
                return self.errorPage("Branch %s/%s doesn't exist" % (reponame, branchname))

            return self.testPageForCommits(
                self.testManager.commitsToDisplayForBranch(branch), 
                "Branch `" + branch.branchname + "`", 
                testGroupsToExpand, 
                branch
                )

    @cherrypy.expose
    def revlist(self, repoName, revlist):
        self.authorize(read_only=True)

        t0 = time.time()
        with self.testManager.database.view():
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
        ungroupedUniqueTestIds = sorted(set(t.testDefinition.name for c in commits for t in self.testManager.database.Test.lookupAll(commitData=c.data)))

        testGroupsToTests = {}
        for testName in ungroupedUniqueTestIds:
            group = testName.split("/")[0]
            if group not in testGroupsToTests:
                testGroupsToTests[group] = []
            testGroupsToTests[group].append(testName)

        testGroupsToExpand = [] if testGroupsToExpand is None else testGroupsToExpand.split(",")

        def appropriateGroup(testName):
            groupPrefix = testName.split("/")[0]
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


    def createGridForBranch(self,
                            branch,
                            testGroups,
                            ungroupedUniqueTestIds,
                            testGroupsToExpand):
        print "HI!: ", testGroups, testGroupsToExpand
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
                                self.branchUrl(branch, [x for x in testGroupsToExpand if x != testGroupPrefix])
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
                            self.branchUrl(branch, testGroupsToExpand + [testGroup])
                            )
                        )
                else:
                    testGroupExpandLinks.append(testGroup)
                
            testGroupExpandLinks[-1] = HtmlGeneration.pad(testGroupExpandLinks[-1], 20)
            testHeaders[-1] = HtmlGeneration.pad(testHeaders[-1], 20)

        grid = [["", "", "", ""] + testHeaders + ["", "", ""]]
        grid.append(
            ["COMMIT", "", "(running)"] + \
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
            return False

            for group in testGroupsToTests[testGroup]:
                if commit.isTargetedTest(group):
                    return True
            return False

        def allTestsInGroupAreTargetedInCommit(commit, testGroup):
            return False

            for group in testGroupsToTests[testGroup]:
                if not commit.isTargetedTest(group):
                    return False
            return True

        row = [self.commitLink(commit)]

        if branch:
            row.append(self.toggleBranchTargetedCommitIdLink(branch, commit))

        row.append(self.testManager.totalRunningCountForCommit(commit) or "")

        if commit.data:
            tests = {t.testDefinition.name: t for t in self.testManager.database.Test.lookupAll(commitData=commit.data)}
        else:
            tests = {}

        class Stat:
            def __init__(self, totalRuns, runningCount, passCount, failCount, errRate):
                self.totalRuns = totalRuns
                self.runningCount = runningCount
                self.passCount = passCount
                self.failCount = failCount
                self.errRate = errRate
                if errRate is None and self.totalRuns:
                    self.errRate = 1.0 - self.passCount / float(self.totalRuns)

            def __add__(self, other):
                if self.totalRuns == 0:
                    blendedErrRate = other.errRate
                elif other.totalRuns == 0:
                    blendedErrRate = self.errRate
                else:
                    blendedErrRate = 1.0 - (1.0 - other.errRate) * (1.0 - self.errRate)

                return Stat(
                    None,
                    self.runningCount + other.runningCount,
                    None,
                    None,
                    blendedErrRate
                    )

        def computeStatForTestGroup(testGroup):
            if testGroup in ungroupedUniqueTestIds:
                return Stat(
                    tests[testGroup].totalRuns,
                    tests[testGroup].activeRuns,
                    tests[testGroup].successes,
                    tests[testGroup].totalRuns - tests[testGroup].successes,
                    None
                    )
            grp = [u for u in ungroupedUniqueTestIds if u.startswith(testGroup+"/")]

            s = computeStatForTestGroup(grp[0])
            for g in grp[1:]:
                s = s + computeStatForTestGroup(g)
            return s

        for testGroup in testGroups:
            stat = computeStatForTestGroup(testGroup)

            if stat.errRate is None:
                if stat.runningCount == 0:
                    row.append("")
                else:
                    row.append("[%s running]" % stat.runningCount)
            else:
                errRate = self.errRateAndTestCount(stat.errRate, stat.totalRuns)

                #check if this point in the commit-sequence has a statistically different
                #probability of failure from its peers and mark it if so.

                if stat.failCount and testGroup in ungroupedUniqueTestIds:
                    row.append(
                        self.commitLink(commit,
                                        failuresOnly=True,
                                        testName=testGroup).withTextReplaced(errRate)
                        )
                else:
                    row.append(HtmlGeneration.lightGrey(errRate))

            if testGroup in ungroupedUniqueTestIds:
                if False: #commit.isTargetedTest(testGroup):
                    row[-1] = HtmlGeneration.blueBacking(row[-1])
            else:
                if allTestsInGroupAreTargetedInCommit(commit, testGroup):
                    row[-1] = HtmlGeneration.blueBacking(row[-1])

                if anyTestInGroupIsTargetedInCommit(commit, testGroup):
                    row[-1] = HtmlGeneration.lightGreyBacking(row[-1])

        row.append(self.sourceLinkForCommit(commit))
        
        row.append(
            HtmlGeneration.lightGrey("waiting to load tests") 
                    if not commit.data
            else HtmlGeneration.lightGrey("invalid test file") 
                    if commit.data.testDefinitionsError
            else self.clearCommitIdLink(commit)
                    if branch
            else ""
            )

        return row

    @staticmethod
    def errRateAndTestCount(errRate, testCount):
        if errRate == 0.0:
            if testCount:
                return "%4s@%3s%s" % (testCount, 0, "%")
            else:
                return "0%"

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

            with self.testManager.database.view():
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
                },
            '/js': {
                'tools.staticdir.on': True,
                'tools.staticdir.dir': os.path.join(current_dir, 'content', 'js')
                }
            })

        cherrypy.server.socket_port = self.httpPort

        cherrypy.engine.autoreload.on = False

        cherrypy.engine.signals.subscribe()

        cherrypy.engine.start()


    @staticmethod
    def stop():
        logging.info("Stopping cherrypy engine")
        cherrypy.engine.exit()
        logging.info("Cherrypy engine stopped")
