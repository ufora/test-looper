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

def secondsUpToString(up_for):
    if up_for < 60:
        return ("%d seconds" % up_for)
    elif up_for < 60 * 60 * 2:
        return ("%.1f minutes" % (up_for / 60))
    elif up_for < 24 * 60 * 60 * 2:
        return ("%.1f hours" % (up_for / 60 / 60))
    else:
        return ("%.1f days" % (up_for / 60 / 60 / 24))

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

    def errorPage(self, errorMessage, currentRepo=None):
        return self.commonHeader(currentRepo=currentRepo) + "\n" + markdown.markdown("#ERROR\n\n" + errorMessage)

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
                self.commonHeader(testRun.test.commitData.commit.repo) +
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
            button_style=self.disable_if_cant_write('btn-danger btn-xs')
            )

    def testLogsButton(self, testId):
        return HtmlGeneration.Link(
            self.testLogsUrl(testId),
            "LOGS", 
            is_button=True,
            button_style=self.disable_if_cant_write('btn-danger btn-xs')
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
            hover_text=hoverOverride or ("" if not commit.data else commit.data.commitMessage)
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
            button_style=self.disable_if_cant_write('btn-danger btn-xs')
            )        
    
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
                
            return self.commonHeader() + HtmlGeneration.grid(grid)

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
                       button_style=self.disable_if_cant_write('btn-danger btn-xs')
                       )
                    )

                row.append(self.environmentLink(t, t.testDefinition.environment_name))

                row.append(str(t.activeRuns))
                row.append(str(t.totalRuns))
                row.append(str(t.totalRuns - t.successes))

                def stringifyPriority(calculatedPriority, priority):
                    if priority.matches.UnresolvedDependencies:
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

            header = self.commonHeader(currentRepo=repoName) + self.commitPageHeader(commit)

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

            header = self.commonHeader(currentRepo=repoName) + header

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
        for dep in self.testManager.database.UnresolvedSourceDependency.lookupAll(test=test):
            grid.append(["Unresolved Repo", dep.repo.name + "/" + dep.commitHash])

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

            return self.commonHeader(currentRepo=repoName) + HtmlGeneration.PreformattedTag(text).render()

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
                'Logout [%s] <span class="glyphicon glyphicon-user" aria-hidden="true"/>'
                '</a>') % self.getCurrentLogin()


    def commonHeader(self, currentRepo=None):
        headers = []
        headers.append(
            '<div align="right"><h5>%s</h5></div>' % (
                self.logout_link() if self.is_authenticated() else self.login_link())
            )

        nav_links = [
            ('Repos', '/repos'),
            ('Machines', '/machines'),
            ('Deployments', '/deployments')
            ]

        if currentRepo:
            if isinstance(currentRepo, unicode):
                reponame = str(currentRepo)
            elif isinstance(currentRepo, str):
                reponame = currentRepo
            else:
                reponame = currentRepo.name

            nav_links.append(("Branches", "/branches?" + urllib.urlencode({"repoName":reponame})))

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

    def toggleCommitUnderTestLink(self, commit):
        actual_priority = commit.userPriority > 0

        icon = "glyphicon-pause" if actual_priority else "glyphicon-play"
        hover_text = "%s testing this commit" % ("Pause" if actual_priority else "Start")
        button_style = "btn-xs " + ("btn-success active" if actual_priority else "btn-default")
        
        return HtmlGeneration.Link(
            "/toggleCommitUnderTest?" + 
                urllib.urlencode({'reponame': commit.repo.name, 'hash':commit.hash, 'redirect': self.redirect()}),
            '<span class="glyphicon %s" aria-hidden="true"></span>' % icon,
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
                if b.head.data.testDefinitions or b.head.data.testDefinitionsError != "No test definition file found.":
                    return True
                return False

            branches = sorted(branches, key=lambda b: (not hasTests(b), b.branchname))

            grid = [["TEST", "BRANCH NAME", "TOP COMMIT", "PINS", "", ""]]

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

                if branch.head.data:
                    pinLines = self.pinGridWithUpdateButtons(branch)

                    if not pinLines:
                        row.extend(["","",""])
                    else:
                        #put the first pin inline here
                        row.extend(pinLines[0])

                        for line in pinLines[1:]:
                            #and the remaining pins on a line each
                            grid.append(["","",""] + line)
                else:
                    row.append("")

            return grid

    def pinGridWithUpdateButtons(self, branch):
        lines = []

        for refname, repoRef in sorted(branch.head.data.repos.iteritems()):
            if repoRef.matches.Pin:
                lines.append(
                    [self.renderPinUpdateLink(branch, refname, repoRef),
                    refname, 
                    self.renderPinReference(refname, repoRef)
                    ])

        return lines
    
    def deployments(self):
        grid = HtmlGeneration.grid(self.deploymentsGrid())
        
        return self.commonHeader() + grid

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
                        button_style=self.disable_if_cant_write('btn-danger btn-xs')
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
            button_style=self.disable_if_cant_write('btn-danger btn-xs')
            )

    def shutdownDeploymentLink(self, d):
        return HtmlGeneration.Link( 
            self.address + "/shutdownDeployment?deploymentId=" + d._identity,
            "shutdown",
            is_button=True,
            new_tab=True,
            button_style=self.disable_if_cant_write('btn-danger btn-xs')
            )

    def shutdownDeployment(self, deploymentId):
        self.testManager.shutdownDeployment(str(deploymentId), time.time())

        raise cherrypy.HTTPRedirect(self.address + "/deployments")

    def repos(self):
        grid = HtmlGeneration.grid(self.reposGrid())
        
        return self.commonHeader() + grid

    def reposGrid(self):
        with self.testManager.database.view():
            repos = self.testManager.database.Repo.lookupAll(isActive=True)
            
            repos = sorted(
                repos, 
                key=lambda repo:
                    (repo.commitsWithTests == 0, repo.name)
                )

            grid = [["REPO NAME", "BRANCH COUNT", "COMMITS", "COMMITS WITH TESTS"]]

            last_repo = None
            for repo in repos:
                if last_repo and last_repo.commitsWithTests and not repo.commitsWithTests:
                    grid.append([""])
                last_repo = repo

                branches = self.testManager.database.Branch.lookupAll(repo=repo)

                grid.append([
                    HtmlGeneration.link(repo.name, "/branches?" + urllib.urlencode({'repoName':repo.name})),
                    str(len(branches)),
                    str(repo.commits),
                    str(repo.commitsWithTests)
                    ])

            return grid

    def branches(self, repoName):
        grid = HtmlGeneration.grid(self.branchesGrid(repoName))
        
        refresh_branches = markdown.markdown(
            "### Refresh Branches button"
            ).replace("button", 
                HtmlGeneration.Link(
                    "/refresh?" + urllib.urlencode({"redirect": self.redirect()}),
                    '<span class="glyphicon glyphicon-refresh " aria-hidden="true" />',
                    is_button=True,
                    button_style='btn-default btn-xs',
                    hover_text='Refresh branches'
                    ).render()
                )

        return self.commonHeader(currentRepo=repoName) + refresh_branches + grid

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

    def branch(self, reponame, branchname, max_commit_count=100):
        t0 = time.time()
        with self.testManager.database.view():
            branch = self.testManager.database.Branch.lookupAny(reponame_and_branchname=(reponame,branchname))

            if branch is None:
                return self.errorPage("Branch %s/%s doesn't exist" % (reponame, branchname))

            return self.testPageForCommits(
                reponame,
                self.testManager.commitsToDisplayForBranch(branch, max_commit_count), 
                "# Branch [%s](%s) / `%s`\n" % (reponame, self.branchesUrl(reponame), branch.branchname),
                branch
                )

    def collapseName(self, name):
        return "/".join([p.split(":")[0] for p in name.split("/")])

    def testPageForCommits(self, reponame, commits, headerText, branch):
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
                ["SOURCE", "", "PINS"]
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

        header = markdown.markdown(headerText)
        
        grid = HtmlGeneration.grid(grid, header_rows=2, rowHeightOverride=33)
        
        canvas = HtmlGeneration.gitgraph_canvas_setup(commit_string, grid)

        return self.commonHeader(reponame) + header + detail_divs + canvas


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

    def aggregateTestInfo(self, testList):
        """Given a bunch of tests, aggregate run information to a single html display."""
        if not testList:
            return ""

        #first check if we have active slaves working
        active = 0
        for t in testList:
            active += t.activeRuns

        no_runs = 0
        for t in testList:
            if t.totalRuns == 0:
                no_runs += 1

        if no_runs:
            if active:
                return "%d running" % active
            else:
                return HtmlGeneration.lightGrey("%d/%d suites unfinished" % (no_runs, len(testList)))

        anyBuilds = bool([t for t in testList if t.testDefinition.matches.Build])
        anyTests = bool([t for t in testList if t.testDefinition.matches.Test])

        if anyBuilds and not anyTests:
            succeeded = 0
            failed = 0
            pending = 0

            for test in testList:
                if test.totalRuns:
                    if test.successes:
                        succeeded += 1
                    elif test.priority.matches.WaitingToRetry:
                        pending += 1
                    else:
                        failed += 1
                else:
                    pending += 1

            if succeeded and not (failed + pending):
                return "%d finished" % succeeded
            if failed and not (succeeded + pending):
                return "%d failed" % failed

            return "%d OK, %d BAD, %d WAIT" % (succeeded, failed, pending)
        else:
            total = 0.0
            failures = 0.0

            for test in testList:
                total += test.totalTestCount / float(test.totalRuns)
                failures += test.totalFailedTestCount / float(test.totalRuns)

            return "%d / %d failing" % (failures, total)

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
            row.append(self.aggregateTestInfo(tests_by_name[env, name]))

        row.append(self.sourceLinkForCommit(commit))
        
        row.append(
            HtmlGeneration.lightGrey("waiting to load tests") 
                    if not commit.data
            else HtmlGeneration.lightGrey("invalid test file") 
                    if commit.data.testDefinitionsError
            else self.clearCommitIdLink(commit)
            )

        pins = []
        if commit.data:
            for repoName, repoRef in sorted(commit.data.repos.iteritems()):
                if repoRef.matches.Pin:
                    result = self.renderPinReference(repoName, repoRef)
                    if not isinstance(result, str):
                        result = result.render()
                    pins.append(result)

        row.append(",&nbsp;".join(pins))

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
                '<span class="glyphicon glyphicon-refresh " aria-hidden="true" />'
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

        return preamble + self.commitLink(commit, textOverride=repoName + "/" + repoRef.branch + branches[repoRef.branch]).render()


    def eventLogs(self):
        return self.commonHeader() + self.generateEventLogHtml(1000)

    def generateEventLogHtml(self, maxMessageCount=10):
        messages = self.eventLog.getTopNLogMessages(maxMessageCount)

        return markdown.markdown("## Most recent actions:\n\n") + HtmlGeneration.grid(
            [["Date", "user", "Action"]] +
            [[msg["date"], msg["user"], msg["message"]] for msg in reversed(messages)]
            )
