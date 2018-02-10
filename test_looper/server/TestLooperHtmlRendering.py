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
import json
import cgi

import test_looper.core.DirectoryScope as DirectoryScope
import test_looper.core.SubprocessRunner as SubprocessRunner
import test_looper.core.source_control as Github
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.core.algebraic_to_json as algebraic_to_json

HtmlGeneration = reload(HtmlGeneration)


def bytesToHumanSize(bytes):
    if bytes is None:
        return ""

    if bytes < 1024 * 2:
        return "%s bytes" % bytes

    if bytes < 1024 * 2 * 1024:
        return "%.1f Kb" % (bytes / 1024.0)

    if bytes < 1024 * 2 * 1024 * 1024:
        return "%.1f Mb" % (bytes / 1024.0 / 1024.0)

    return "%.1f Gb" % (bytes / 1024.0 / 1024.0 / 1024.0)


def card(text):
    return """<div class="card">
                  <div class="card-body">
                    {text}
                  </div>
                </div>""".format(text=text)

def tabs(name, tabSeq):
    pils = []
    bodies = []

    for ix in xrange(len(tabSeq)):
        header, contents, selector = tabSeq[ix]

        active = "active" if ix == 0 else ""
        pils.append(
            """
            <li class="nav-item">
                <a class="nav-link {active}" id="{selector}-tab" data-toggle="tab" href="#{selector}" role="tab" aria-controls="{selector}" aria-selected="{selected}">
                    {header}
                </a>
              </li>
            """.format(active=active, selector=selector, header=header, selected=ix==0)
            )

        bodies.append(
            """
            <div class="tab-pane fade {show} {active}" id="{selector}" role="tabpanel" aria-labelledby="{selector}-tab">{contents}</div>
            """.format(selector=selector,contents=contents, active=active, show="show" if ix == 0 else "")
            )

    return ("""<div class="container-fluid mb-3">
                     <ul class="nav nav-pills" id="{name}" role="tablist">
                      {pils}
                    </ul>
                    <div class="tab-content" id="{name}Content">
                      {body}
                    </div>
                </div>
                """.format(pils="".join(pils), body="".join(bodies),name=name))


def octicon(text, extra=""):
    return '<span class="octicon octicon-%s %s" aria-hidden="true"/>' % (text,extra)

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
        active = sum(t.activeRuns for t in self.tests)
        if active:
            return "%s running" % max(active,0)

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

        #disable for now
        return name

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
        return (
            HtmlGeneration.headers + 
            f(self, *args, **kwargs) +
            HtmlGeneration.footers
            )
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

    def commonHeader(self, currentObject):
        headers = []

        nav_links = [
            ('Machines', '/machines', currentObject == "machines", []),
            ('Deployments', '/deployments', currentObject == "deployments", []),
            ('Repos', '/repos', currentObject == "repos",[]),
            ('<span class="px-4"/>', False, False,[]),
            ]

        arrow_link_ix = len(nav_links)-1

        def addRepo(repo, isActive):
            dds = []

            dds.append((self.branchesUrl(repo.name),repo.name))
            dds.append('<div class="dropdown-divider"></div>')

            for r in sorted(self.testManager.database.Repo.lookupAll(isActive=True),key=lambda r:r.name):
                if r.commitsWithTests and r != repo:
                    dds.append((self.branchesUrl(r.name), r.name))

            nav_links.append(
                    (octicon('repo') + repo.name, "", isActive, dds)
                    )

        def addSpacer():
            nav_links.append(("/", "",False,[]))

        def addBranch(branch, isActive):
            addRepo(branch.repo, False)
            addSpacer()

            dds = []

            dds.append((self.branchUrl(branch),branch.branchname))
            dds.append('<div class="dropdown-divider"></div>')

            for b in sorted(self.testManager.database.Branch.lookupAll(repo=branch.repo),key=lambda b:b.branchname):
                if self.branchHasTests(b) and b != branch:
                    dds.append((self.branchUrl(b),b.branchname))

            if len(dds) == 2:
                dds = []

            if not dds:
                link = self.branchUrl(branch)
            else:
                link = ""

            nav_links.append(
                    ('<span class="octicon octicon-git-branch" aria-hidden="true"/>' + branch.branchname, link, isActive, dds)
                    )

        def addCommit(commit, isActive):
            branch, name = self.testManager.bestCommitBranchAndName(commit)
            if branch:
                addBranch(branch, False)
            else:
                addRepo(commit.repo, False)

            addSpacer()

            nav_links.append(
                    ('Commit&nbsp;<span class="octicon octicon-git-commit" aria-hidden="true"/>' + "HEAD"+name, 
                        self.commitUrl(commit), 
                        isActive, [])
                    )

        def addTest(test, isActive):
            commit = test.commitData.commit

            addCommit(commit, False)
            branch, name = self.testManager.bestCommitBranchAndName(commit)
            addSpacer()

            if test.testDefinition.matches.Build:
                icon = 'tools'
            else:
                icon = "beaker"

            nav_links.append(
                    (octicon(icon) + '<span class="px-1"/>' + test.testDefinition.name, 
                        self.testUrl(test)
                        , isActive, [])
                    )

        def addTestRun(testRun, isActive):
            addTest(testRun.test, False)
            addSpacer()
            nav_links.append(
                (octicon("file-directory") + '<span class="px-1"/>' + testRun._identity[:8], "", isActive, [])
                )
            
        if currentObject:
            if isinstance(currentObject, self.testManager.database.Repo):
                addRepo(currentObject, True)

            if isinstance(currentObject, self.testManager.database.Branch):
                addBranch(currentObject, False)

            if isinstance(currentObject, self.testManager.database.Commit):
                commit = currentObject
                addCommit(commit, True)

            if isinstance(currentObject, self.testManager.database.Test):
                addTest(currentObject, True)

            if isinstance(currentObject, self.testManager.database.TestRun):
                addTestRun(currentObject, True)
                
        
        headers += ["""
            <nav class="navbar navbar-expand navbar-light bg-light">
            <button class="navbar-toggler" type="button" data-toggle="collapse" data-target="#navbarText" aria-controls="navbarText" aria-expanded="false" aria-label="Toggle navigation">
              <span class="navbar-toggler-icon"></span>
            </button>
            <ul class="navbar-nav mr-auto">
            """]

        for label, link, active, dropdowns in nav_links:
            elt = label
            if link:
                elt = '<a class="nav-link" href="{link}">{elt}</a>'.format(link=link,elt=elt)
            else:
                if not dropdowns:
                    elt = '<div class="navbar-text">{elt}</div>'.format(elt=elt)

            if dropdowns:
                dd_items = []
                for item in dropdowns:
                    if isinstance(item,str):
                        dd_items += [item]
                    else:
                        href, contents = item
                        dd_items += [
                            '<a class="dropdown-item" href="{link}">{contents}</a>'.format(link=href,contents=contents)
                            ]

                elt = """
                    <div class="btn-group">
                      <button class="btn {btnstyle} dropdown-toggle" type="button" id="dropdownMenuButton" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false">
                        {elt}
                      </button>
                      <div class="dropdown-menu" aria-labelledby="dropdownMenuButton">
                        {dd_items}
                      </div>
                      
                    </div>
                    """.format(
                        elt=elt, 
                        dd_items = "".join(dd_items),
                        btnstyle="btn-outline-secondary" if not active else "btn-primary"
                        )

            elt = ('<li class="nav-item {is_active} px-md-1">{elt}</li>'.format(elt=elt, is_active="active" if active else ""))

            headers += [elt]

        headers += [
            '</ul>'
            ]

        if currentObject == "repos":
            headers += [
                """<span class="navbar-text pr-5"><button class="btn btn-outline-secondary btn-light">
                    <a href="{url}"><span class="octicon octicon-sync" aria-hidden="true"/> Refresh {kind}</a>
                    </button></span>
                """.format(
                    url="/refresh?" + urllib.urlencode({"redirect": self.redirect()}),
                    kind="Repos"
                    )
                ]

        headers += [
            '<span class="navbar-text">',
                self.logout_link() if self.is_authenticated() else self.login_link(),
            '</span>',
            '</nav>']
        return "\n" + "\n".join(headers)
    
    def wrapInHeader(self, contents, breadcrumb):
        return self.commonHeader(breadcrumb) + (
            '<main class="py-md-5"><div class="container-fluid">' + contents + "</div></main>"
            )            

    @HtmlWrapper
    def errorPage(self, errorMessage, breadcrumb):
        return self.wrapInHeader(
            markdown.markdown("#ERROR\n\n" + errorMessage),
            breadcrumb
            )

    @HtmlWrapper
    def test(self, testId):
        with self.testManager.database.view():
            testRun = self.testManager.getTestRunById(testId)

            if testRun is None:
                return self.errorPage("Unknown testid %s" % testId)

            artifacts = self.artifactsForTestRunGrid(testRun)

            individualTestReport = self.individualTestReport(testRun)

            return self.wrapInHeader(
                tabs("test_tab", [
                    ("Artifacts", artifacts, "artifacts"),
                    ("Individual Tests", individualTestReport, "tests_individual")
                    ]),
                testRun
                )

    def individualTestReport(self, testRun):
        if testRun.totalTestCount:
            individual_tests_grid = [["TEST_NAME", "PASSED"]]
            pass_dict = {}

            for ix in xrange(len(testRun.testNames.test_names)):
                pass_dict[testRun.testNames.test_names[ix]] = "PASS" if testRun.testFailures[ix] else "FAIL"

            for k,v in sorted(pass_dict.items()):
                individual_tests_grid.append((k,v))

            return HtmlGeneration.grid(individual_tests_grid)
        else:
            return card("No Individual Tests Reported")

    def artifactsForTestRunGrid(self, testRun):
        grid = [["Artifact", "Size"]]

        commit = testRun.test.commitData.commit

        if testRun.test.testDefinition.matches.Build:
            build_key = testRun.test.testDefinition.name.replace("/","_") + ".tar.gz"

            if self.artifactStorage.build_exists(commit.repo.name, commit.hash, build_key):
                grid.append([
                    HtmlGeneration.link(build_key, self.buildDownloadUrl(commit.repo.name, commit.hash, build_key)),
                    bytesToHumanSize(self.artifactStorage.build_size(commit.repo.name, commit.hash, build_key))
                    ])
            else:
                logging.info("No build found at %s", build_key)

        for artifactName, sizeInBytes in self.artifactStorage.testResultKeysForWithSizes(commit.repo.name, commit.hash, testRun._identity):
            grid.append([
                HtmlGeneration.link(
                    artifactName,
                    self.testResultDownloadUrl(testRun._identity, artifactName)
                    ),
                bytesToHumanSize(sizeInBytes)
                ])

        if not grid:
            return card("No Test Artifacts produced")

        return HtmlGeneration.grid(grid)


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

    def testUrl(self, test):
        return "/allTestRuns?" + urllib.urlencode({
            "repoName": test.commitData.commit.repo.name,
            "commitHash": test.commitData.commit.hash,
            "testName": test.testDefinition.name
            })

    def testLink(self, text, commit, testName, failuresOnly=False):
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

    def wellNamedCommitLinkAsStr(self, commit, branch=None, branchExtension=None, excludeRepo=False):
        if not branch:
            branch, branchExtension = self.testManager.bestCommitBranchAndName(commit)

            if not branch:
                return (
                    (self.branchesLink(commit.repo.name).render() + "/" if not excludeRepo else "") + 
                        self.commitLink(commit, commit.hash[:10])
                    ).render()

        if not branchExtension:
            branchExtension = "/HEAD"

        return (
            (self.branchesLink(branch.repo.name).render() + "/" if not excludeRepo else "") + 
            self.branchLink(branch).render() + 
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

        return HtmlGeneration.link(
            text,
            self.commitUrl(commit),
            hover_text=hoverOverride or ("commit " + commit.hash[:10] + " : " + ("" if not commit.data else commit.data.commitMessage))
            )

    def commitUrl(self, commit):
        extras = {}
        extras["repoName"] = commit.repo.name
        extras["commitHash"] = commit.hash

        return self.address + "/commit" + ("?" if extras else "") + urllib.urlencode(extras)

    def branchLink(self, branch):
        return HtmlGeneration.link(branch.branchname, self.branchUrl(branch))

    def branchUrl(self, branch):
        args = {"reponame": branch.repo.name, "branchname": branch.branchname}
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

            grid = [["MachineID", "Hardware", "OS", "UP FOR", "STATUS", "LASTMSG", "COMMIT", "TEST", "LOGS", "CANCEL", ""]]
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

                row.append(secondsUpToString(time.time() - m.bootTime))
                
                if m.firstHeartbeat < 1.0:
                    row.append('<span class="octicon octicon-watch" aria-hidden="true"></span>')
                elif time.time() - m.lastHeartbeat < 60:
                    row.append('<span class="octicon octicon-check" aria-hidden="true"'
                        + ' data-toggle="tooltip" data-placement="right" title="Heartbeat %s seconds ago" ' % (int(time.time() - m.lastHeartbeat))
                        + '></span>'
                        )
                else:
                    row.append('<span class="octicon octicon-issue-opened" aria-hidden="true"'
                        + ' data-toggle="tooltip" data-placement="right" title="Heartbeat %s seconds ago" ' % (int(time.time() - m.lastHeartbeat))
                        + '></span>'
                        )
                
                row.append(m.lastHeartbeatMsg)

                tests = self.testManager.database.TestRun.lookupAll(runningOnMachine=m)
                deployments = self.testManager.database.Deployment.lookupAll(runningOnMachine=m)

                if len(tests) + len(deployments) > 1:
                    row.append("ERROR: multiple test runs/deployments")
                elif tests:
                    commit = tests[0].test.commitData.commit
                    try:
                        row.append(self.wellNamedCommitLinkAsStr(commit))
                    except:
                        row.append("")

                    row.append(self.testRunLink(tests[0], tests[0].test.testDefinition.name))
                    row.append(self.testLogsButton(tests[0]._identity))
                    row.append(self.cancelTestRunButton(tests[0]._identity))
                    
                elif deployments:
                    commit = deployments[0].test.commitData.commit
                    try:
                        row.append(self.wellNamedCommitLinkAsStr(commit))
                    except:
                        row.append("")

                    d = deployments[0]
                    row.append("DEPLOYMENT")
                    row.append(self.connectDeploymentLink(d))
                    row.append(self.shutdownDeploymentLink(d))
                
                grid.append(row)
                
            return self.wrapInHeader(
                HtmlGeneration.grid(grid),
                "machines"
                )

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

            commitTestGrid = self.commitTestGrid(commit)

            res = tabs("commit", [
                ("Tests", HtmlGeneration.grid(commitTestGrid), "commit_tests"),
                ("Test Definitions", self.commitTestDefinitionsInfo(commit), "commit_test_defs")
                ])

            return self.wrapInHeader(res, commit)


    def commitTestDefinitionsInfo(self, commit):
        raw_text, extension = self.testManager.getRawTestFileForCommit(commit)

        return card('<pre class="language-yaml"><code class="line-numbers">%s</code></pre>' % cgi.escape(raw_text))


    def commitTestGrid(self, commit):
        tests = self.testManager.database.Test.lookupAll(commitData=commit.data)
        
        if not tests:
            return card("Commit defined no tests. Maybe look at the test definitions?")

        tests = sorted(tests, key=lambda test: test.fullname)
        
        grid = [["TEST", "", "", "ENVIRONMENT", "RUNNING", "COMPLETED", "FAILED", "PRIORITY", "AVG_TEST_CT", "AVG_FAILURE_CT", "AVG_RUNTIME", "", "TEST_DEPS"]]

        for t in tests:
            row = []

            row.append(
                self.testLink(t.testDefinition.name, commit, t.testDefinition.name)
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

        return grid
    
    def testDependencySummary(self, t):
        """Return a single cell displaying all the builds this test depends on"""
        return TestSummaryRenderer(self, self.testManager.allTestsDependedOnByTest(t)).renderSummary()

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

            test = [x for x in 
                self.testManager.database.Test.lookupAll(commitData=commit.data)
                    if x.testDefinition.name == testName][0]

            testRuns = self.testManager.database.TestRun.lookupAll(test=test)

            grid = self.gridForTestList_(testRuns, commit=commit, failuresOnly=failuresOnly)

            testDeps = HtmlGeneration.grid(self.allTestDependencyGrid(test))

            if len(testRuns) == 1:
                extra_tabs = [
                    ("Artifacts", self.artifactsForTestRunGrid(testRuns[0]), "artifacts"),
                    ("Individual Tests", self.individualTestReport(testRuns[0]), "tests_individual")
                    ]
            else:
                extra_tabs = []

            return self.wrapInHeader(
                tabs("test", [
                    ("Individual Test Runs", HtmlGeneration.grid(grid), "testruns"), 
                    ("Test Dependencies", testDeps, "testdeps")
                    ] + extra_tabs),
                test
                )

    def allTestDependencyGrid(self, test):
        grid = [["COMMIT", "TEST", ""]]

        for subtest in self.testManager.allTestsDependedOnByTest(test):
            grid.append([
                self.wellNamedCommitLinkAsStr(subtest.commitData.commit),
                    self.testLink(subtest.testDefinition.name, subtest.commitData.commit, subtest.testDefinition.name),
                    TestSummaryRenderer(self, [test]).renderSummary()
                ])

        for dep in self.testManager.database.UnresolvedTestDependency.lookupAll(test=test):
            grid.append(["Unresolved Test", dep.dependsOnName,""])
        for dep in self.testManager.database.UnresolvedSourceDependency.lookupAll(test=test):
            grid.append(["Unresolved Commit", dep.repo.name + "/" + dep.commitHash, ""])
        for dep in self.testManager.database.UnresolvedRepoDependency.lookupAll(test=test):
            grid.append(["Unresolved Repo", dep.reponame + "/" + dep.commitHash, ""])

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

            return self.wrapInHeader(
                HtmlGeneration.PreformattedTag(text).render(),
                commit
                )

    def testRunLink(self, testRun, text_override=None):
        return HtmlGeneration.link(text_override or str(testRun._identity)[:8], "/test?testId=" + testRun._identity)

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

    def branchHasTests(self, b):
        if not b.head or not b.head.data:
            return False
        if b.head.data.testDefinitions or (
                b.head.data.testDefinitionsError != "No test definition file found."
                and not b.head.data.testDefinitionsError.startswith("Commit old")
                ):
            return True
        return False

    def branchesGrid(self, repoName, groupingInstructions):
        groupingInstructions = None
        
        t0 = time.time()
        with self.testManager.database.view():
            lock_time = time.time()
            repo = self.testManager.database.Repo.lookupOne(name=repoName)

            branches = self.testManager.database.Branch.lookupAll(repo=repo)
            
            branches = sorted(branches, key=lambda b: (not self.branchHasTests(b), b.branchname))

            
            test_rows = {}
            best_commit = {}
            best_commit_name = {}

            for b in branches:
                best_commit[b],best_commit_name[b] = self.bestCommitForBranch(b)

                test_rows[b] = self.allTestsForCommit(best_commit[b]) if best_commit[b] else []

            def branchesUrlWithGroupings(newInstructions):
                return self.branchesUrl(repoName, json.dumps(newInstructions))

            renderer = TestGridRenderer(test_rows, list(branches), groupingInstructions)

            grid_headers = renderer.getGridHeaders(branchesUrlWithGroupings)

            if grid_headers:
                for additionalHeader in reversed(["TEST", "BRANCH NAME", "TOP COMMIT", "TOP TESTED COMMIT"]):
                    grid_headers = [[""] + g for g in grid_headers]
                    grid_headers[-1][0] = additionalHeader
            else:
                grid_headers = [["TEST", "BRANCH NAME", "TOP COMMIT", "TOP TESTED COMMIT"]]

            grid = []

            lastBranch = None
            for branch in branches:
                if lastBranch is not None and not self.branchHasTests(branch) and self.branchHasTests(lastBranch):
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

                row.append(self.wellNamedCommitLinkAsStr(best_commit[branch], branch, best_commit_name[branch],excludeRepo=True))

                row.extend(renderer.render_row(branch, branchesUrlWithGroupings))

            return grid_headers, grid

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
        
        return self.wrapInHeader(grid, "deployments")

    def branchesLink(self, reponame, text=None):
        return HtmlGeneration.link(text or reponame, self.branchesUrl(reponame))

    def branchesUrl(self, reponame, groupings=None):
        if isinstance(reponame, self.testManager.database.Repo):
            reponame = reponame.name

        return self.address + "/branches?" + urllib.urlencode({'repoName':reponame,'groupings':groupings})

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
        
        return self.wrapInHeader(grid, "repos")

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

        if not self.branchHasTests(branch):
            return branch.head, ""

        c = branch.head
        commits = []
        lookbacks = 0

        while not self.allTestsHaveRun(c):
            if c.data and c.data.parents:
                c = c.data.parents[0]
                lookbacks += 1

                if lookbacks > 50:
                    return branch.head, ""
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

    def reposUrl(self, newInstructions=None):
        return "/repos?" + urllib.urlencode({'groupings':json.dumps(newInstructions)} if newInstructions else {})

            
    def reposGrid(self, groupingInstructions):
        groupingInstructions = None

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

            renderer = TestGridRenderer(test_rows, list(repos), groupingInstructions)

            grid_headers = renderer.getGridHeaders(self.reposUrl)

            for additionalHeader in reversed(["REPO NAME", "BRANCH COUNT", "COMMITS", "TOP TESTED COMMIT"]):
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
                    testRow = renderer.render_row(repo, self.reposUrl)
                else:
                    testRow = [""] * len(renderer.columnsInOrder())

                grid.append([
                    HtmlGeneration.link(repo.name, "/branches?" + urllib.urlencode({'repoName':repo.name})),
                    str(len(branches)),
                    str(repo.commits),
                    self.wellNamedCommitLinkAsStr(best_commit[repo], best_branch[repo], best_commit_name[repo], excludeRepo=True) if best_commit[repo] else ""
                    ] + testRow)

            return grid_headers, grid

    @HtmlWrapper
    def branches(self, repoName, groupings=None):
        headers, grid = self.branchesGrid(repoName, groupings)

        grid = HtmlGeneration.grid(headers+grid, header_rows=len(headers))
        
        with self.testManager.database.view():
            return self.wrapInHeader(
                grid, 
                [self.testManager.database.Repo.lookupOne(name=repoName)]
                )

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
                    HtmlGeneration.grid(pinGrid)
                    )
            else:
                pinContents = card("Branch has no pins.")

            commitContents = self.testDisplayForCommits(
                reponame,
                self.testManager.commitsToDisplayForBranch(branch, max_commit_count), 
                branch
                )

            return self.wrapInHeader(
                tabs("branchtab", [("Commits", commitContents, 'commit'), ("Branch Pins", pinContents, 'pins')]),
                branch
                )

    def collapseName(self, name, env):
        name = "/".join([p.split(":")[0] for p in name.split("/")])
        env = env.split("/")[-1]
        if name.endswith("/" + env):
            name = name[:-1-len(env)]
        return name

    def testDisplayForCommits(self, reponame, commits, branch):
        test_env_and_name_pairs = set()

        for c in commits:
            for test in self.testManager.database.Test.lookupAll(commitData=c.data):
                if not test.testDefinition.matches.Deployment:
                    test_env_and_name_pairs.add((test.testDefinition.environment_name, test.testDefinition.name))

        #this is how we will aggregate our tests
        envs_and_collapsed_names = sorted(
            set([(env, self.collapseName(name, env)) for env, name in test_env_and_name_pairs])
            )

        collapsed_name_environments = []
        for env, name in envs_and_collapsed_names:
            if not collapsed_name_environments or collapsed_name_environments[-1]["content"] != env:
                collapsed_name_environments.append({"content": env, "colspan": 1})
            else:
                collapsed_name_environments[-1]["colspan"] += 1

        for env in collapsed_name_environments:
            env["content"] = '<div class="border %s text-center">%s</div>' % (
                "border-dark" if env["colspan"]>1 else "", env["content"]
                )

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

        grid = HtmlGeneration.grid(grid, header_rows=2, rowHeightOverride=36)
        
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
                            self.collapseName(t.testDefinition.name, t.testDefinition.environment_name))
                    tests_by_name[env_name_pair].append(t)
        
        for env, name in envs_and_collapsed_names:
            row.append(TestSummaryRenderer(self, tests_by_name[env, name]).renderSummary())

        row.append(self.sourceLinkForCommit(commit))
        
        row.append(
            HtmlGeneration.lightGrey("waiting to load tests") 
                    if not commit.data
            else HtmlGeneration.lightGrey("invalid test file") 
                    if commit.data.testDefinitionsError
            else ""
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
