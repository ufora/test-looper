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
import traceback
import markdown
import urllib
import urlparse
import pytz
import simplejson
import struct
import os
import json
import cgi

import test_looper.core.tools.Git as Git
import test_looper.core.SubprocessRunner as SubprocessRunner
import test_looper.core.source_control as Github
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.core.algebraic_to_json as algebraic_to_json

import test_looper.server.rendering.ComboContexts as ComboContexts
import test_looper.server.rendering.RootContext as RootContext
import test_looper.server.rendering.ReposContext as ReposContext
import test_looper.server.rendering.MachinesContext as MachinesContext
import test_looper.server.rendering.AmisContext as AmisContext
import test_looper.server.rendering.DeploymentsContext as DeploymentsContext
import test_looper.server.rendering.BranchContext as BranchContext
import test_looper.server.rendering.IndividualTestContext as IndividualTestContext
import test_looper.server.rendering.CommitContext as CommitContext
import test_looper.server.rendering.RepoContext as RepoContext
import test_looper.server.rendering.TestContext as TestContext
import test_looper.server.rendering.TestRunContext as TestRunContext

import re

class Renderer:
    def __init__(self, httpServer):
        self.httpServer = httpServer
        self.httpServerConfig = httpServer.httpServerConfig
        self.testManager = httpServer.testManager
        self.artifactStorage = httpServer.artifactStorage
        self.address = httpServer.address
        self.src_ctrl = httpServer.src_ctrl

    def repoDisplayName(self, reponame):
        for prefix in self.httpServerConfig.repo_prefixes_to_shorten:
            if reponame.startswith(prefix):
                return reponame[len(prefix):]
        return reponame

    def wantsToShowRepo(self, repo):
        if not isinstance(repo, str):
            repo = repo.name


        for prefixToExclude in self.httpServerConfig.repo_prefixes_to_suppress:
            if repo.startswith(prefixToExclude):
                return False

        return True


    def contextFor(self, entity, options):
        if entity == "root":
            return RootContext.RootContext(self, options)
        if entity == "repos":
            return ReposContext.ReposContext(self, options)
        if entity == "machines":
            return MachinesContext.MachinesContext(self, options)
        if entity == "amis":
            return AmisContext.AmisContext(self, options)
        if entity == "deployments":
            return DeploymentsContext.DeploymentsContext(self, options)

        if isinstance(entity, self.testManager.database.Branch):
            return BranchContext.BranchContext(self, entity, "", "", options)
        if isinstance(entity, ComboContexts.BranchAndFilter):
            return BranchContext.BranchContext(self, entity.branch, entity.configurationName, entity.projectName, options)

        if isinstance(entity, self.testManager.database.Commit):
            return CommitContext.CommitContext(self, entity, "", "", options)
        if isinstance(entity, ComboContexts.CommitAndFilter):
            return CommitContext.CommitContext(self, entity.commit, entity.configurationName, entity.projectName, options)

        mapping = {
            self.testManager.database.Repo: RepoContext.RepoContext,
            ComboContexts.IndividualTest: IndividualTestContext.IndividualTestContext,
            self.testManager.database.Commit: CommitContext.CommitContext,
            self.testManager.database.Test: TestContext.TestContext,
            self.testManager.database.TestRun: TestRunContext.TestRunContext
            }

        for k,v in mapping.items():
            if isinstance(entity, k):
                return v(self, entity, options)

        assert False, entity

    def can_write(self):
        return self.httpServer.can_write()

    def is_authenticated(self):
        return self.httpServer.is_authenticated()

    def getCurrentLogin(self):
        return self.httpServer.getCurrentLogin()


    def test_contents(self, testId, key):
        with self.testManager.database.view():
            testRun = self.testManager.getTestRunById(testId)

            assert testRun

            return self.processFileContents(
                self.artifactStorage.testContentsHtml(testRun.test.hash, testId, key)
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

            if testRun.test.testDefinitionSummary.type == "Build":
                for artifact in testRun.test.testDefinitionSummary.artifacts:
                    full_name = testRun.test.testDefinitionSummary.name + ("/" + artifact if artifact else "")

                    build_key = self.artifactStorage.sanitizeName(full_name) + ".tar.gz"

                    self.artifactStorage.clear_build(testRun.test.hash, full_name)

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

    def build_contents(self, testHash, key):
        return self.processFileContents(self.artifactStorage.buildContentsHtml(testHash, key))

    def buildDownloadUrl(self, testHash, key):
        return self.address + "/build_contents?" + urllib.urlencode({"testHash": testHash, "key": key})

    def wrapInHeader(self, contents, breadcrumb):
        return self.commonHeader(breadcrumb) + (
            '<main class="py-md-5"><div class="container-fluid">' + contents + "</div></main>"
            )            

    def errorPage(self, errorMessage):
        return (
            HtmlGeneration.headers + 
            """
            <div class="container">
                <div class="header clearfix mb-5"></div>
                    <div class="jumbotron">
                        <div class="display-3 text-center">
                            <h1>
                                %s</h1>
                        </div>
                    </div>
            </div>
            """ % errorMessage +
            HtmlGeneration.footers
            )

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

    def bootDeployment(self, testHash):
        try:
            deploymentId = self.testManager.createDeployment(testHash, time.time())
        except Exception as e:
            logging.error("Failed to boot a deployment:\n%s", traceback.format_exc())
            return self.errorPage("Couldn't boot a deployment for %s: %s" % (testHash, str(e)))

        logging.info("Redirecting for %s", testHash)
        
        raise cherrypy.HTTPRedirect(self.address + "/terminalForDeployment?deploymentId=" + deploymentId)

    def testEnvironment(self, repoName, commitHash, environmentName):
        with self.testManager.database.view():
            repo = self.testManager.database.Repo.lookupAny(name=repoName)
            if not repo:
                return self.errorPage("Repo %s doesn't exist" % repoName)

            commit = self.testManager.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))
            if not commit or not commit.data:
                return self.errorPage("Commit %s/%s doesn't exist" % (repoName, commitHash))

            _, envs, _ = self.testManager._extractCommitTestsEnvsAndRepos(commit)

            env = envs.get(environmentName)

            if not env:
                return self.errorPage("Environment %s/%s/%s doesn't exist" % (repoName, commitHash, environmentName))

            text = algebraic_to_json.encode_and_dump_as_yaml(env)

            return card('<pre class="language-yaml"><code class="line-numbers">%s</code></pre>' % cgi.escape(text))

    def testRunLink(self, testRun, text_override=None):
        return HtmlGeneration.link(text_override or str(testRun._identity)[:8], "/test?testId=" + testRun._identity)


    def login_link(self):
        return '<a href="%s">Login</a>' % self.src_ctrl.authenticationUrl()

    def logout_link(self):
        return ('<a href="/logout">'
                'Logout [<span class="octicon octicon-person" aria-hidden="true"></span>%s]'
                '</a>') % self.getCurrentLogin()

    def reload_link(self):
        return HtmlGeneration.Link(
            "/reloadSource?" + 
                urllib.urlencode({'redirect': self.redirect()}),
            '<span class="octicon octicon-sync" aria-hidden="true" style="horizontal-align:center"></span>',
            is_button=True,
            button_style='btn-outline-primary btn-xs'
            )

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
        if b.head.data.noTestsFound:
            return False
        return True

    def branchesLink(self, reponame, text=None):
        return HtmlGeneration.link(text or reponame, self.branchesUrl(reponame))

    def branchesUrl(self, reponame, groupings=None):
        if isinstance(reponame, self.testManager.database.Repo):
            reponame = reponame.name

        return self.address + "/branches?" + urllib.urlencode({'repoName':reponame,'groupings':groupings})

    def shutdownDeployment(self, deploymentId):
        self.testManager.shutdownDeployment(str(deploymentId), time.time())

        raise cherrypy.HTTPRedirect(self.address + "/deployments")

    def allTestsForCommit(self, commit):
        if not commit.data:
            return []
        return [x for x in self.testManager.allTestsForCommit(commit) if not x.testDefinitionSummary.disabled]

    def bestCommitForBranch(self, branch):
        if not branch or not branch.head or not branch.head.data:
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

        tests = self.testManager.allTestsForCommit(commit)
        if not tests:
            return False

        for test in tests:
            if not test.testDefinitionSummary.type == "Deployment" and not test.testDefinitionSummary.disabled:
                if test.totalRuns == 0 or test.priority.matches.WaitingToRetry:
                    if not test.priority.matches.DependencyFailed:
                        return False

        return True

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

    def default(self, *args, **kwargs):
        if args:
            if 'action' in kwargs:
                database_scope = self.testManager.transaction_and_lock()
            else:
                database_scope = self.testManager.database.view()

            with database_scope:
                context = self.getFromEncoding(args, kwargs)
                if context:
                    t0 = time.time()
                    try:
                        return context.renderWholePage()
                    finally:
                        logging.info("Rendering page for %s, %s took %s", args, kwargs, time.time()-t0)

        return self.errorPage("Invalid URL provided")

    def getFromEncoding(self, path, argDict):
        context = RootContext.RootContext(self, {})

        while path:
            context, path = context.consumePath(path)
            if not context:
                return None

        return context.withOptions(**argDict)

