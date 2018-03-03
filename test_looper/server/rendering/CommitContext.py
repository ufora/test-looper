import test_looper.server.rendering.Context as Context
import test_looper.server.rendering.ComboContexts as ComboContexts
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.data_model.BranchPinning as BranchPinning
import test_looper.server.rendering.IndividualTestGridRenderer as IndividualTestGridRenderer
import logging
import urllib
import cgi
import time
import uuid
import textwrap

octicon = HtmlGeneration.octicon
card = HtmlGeneration.card

class CommitContext(Context.Context):
    def __init__(self, renderer, commit, options):
        Context.Context.__init__(self, renderer, options)
        self.reponame = commit.repo.name
        self.commitHash = commit.hash
        self.options = options

        self.repo = commit.repo
        self.commit = commit        
        self._nameInBranch = None
        self._branch = None

    @property
    def branch(self):
        if self._branch is None:
            self._branch, self._nameInBranch = self.testManager.bestCommitBranchAndName(self.commit)
        return self._branch

    @property
    def nameInBranch(self):
        if self._branch is None:
            self._branch, self._nameInBranch = self.testManager.bestCommitBranchAndName(self.commit)
        return self._nameInBranch

    def isPinUpdateCommit(self):
        if not self.commit.data.commitMessage.startwith("Updating pin"):
            return False
    
    def consumePath(self, path):
        if path and path[0] == "configurations":
            groupPath, remainder = self.popToDash(path[1:])

            if not path:
                return None, path

            configurationName = "/".join(groupPath)

            return self.contextFor(ComboContexts.CommitAndConfiguration(self.commit, configurationName)), remainder

        if path and path[0] == "tests":
            testpath, remainder = self.popToDash(path[1:])

            fullname = self.commit.repo.name + "/" + self.commit.hash + "/" + "/".join(testpath)

            test = self.database.Test.lookupAny(fullname=fullname)

            if not test:
                return None, path

            return self.renderer.contextFor(test, self.options), remainder

        return None, path

    def toggleCommitUnderTestLink(self):
        commit = self.commit

        actual_priority = commit.userPriority > 0

        icon = "octicon-triangle-right"
        hover_text = "%s tests for this commit" % ("Enable" if not actual_priority else "Disable")
        button_style = "btn-xs " + ("btn-primary active" if actual_priority else "btn-outline-dark")
        
        return HtmlGeneration.Link(
            "/toggleCommitUnderTest?" + 
                urllib.urlencode({'reponame': commit.repo.name, 'hash':commit.hash, 'redirect': self.redirect()}),
            '<span class="octicon %s" aria-hidden="true"></span>' % icon,
            is_button=True,
            button_style=self.renderer.disable_if_cant_write(button_style),
            hover_text=hover_text
            )
    
    def renderLinkToSCM(self):
        url = self.renderer.src_ctrl.commit_url(self.commit.repo.name, self.commit.hash)
        return HtmlGeneration.link(octicon("diff"), url, hover_text="View diff")

    
    def renderNavbarLink(self):
        return octicon("git-commit") + self.renderLink(includeBranch=False, includeRepo=False)

    def recency(self):
        return '<span class="text-muted">%s</span>' % (HtmlGeneration.secondsUpToString(time.time() - self.commit.data.timestamp) + " ago")

    def renderLinkWithShaHash(self, noIcon=False):
        if not self.commit.data:
            return ''

        return (octicon("git-commit") if not noIcon else "") + HtmlGeneration.link(
                "<code>" + self.commit.hash[:8] + "</code>",
                self.urlString(),
                hover_text=("commit " + self.commit.hash[:10] + " : " + ("" if not self.commit.data else self.commit.data.commitMessage))
                )

    def renderSubjectAndAuthor(self, maxChars=40):
        if not self.commit.data:
            return ""

        pinUpdate = BranchPinning.unpackCommitPinUpdateMessage(self.commit.data.commitMessage)

        if pinUpdate:
            repo, branch, hash = pinUpdate
            underlying_commit = self.testManager._lookupCommitByHash(repo, hash, create=False)
            if underlying_commit:
                underlyingCtx = self.contextFor(underlying_commit)
                underRepo = self.contextFor(underlying_commit.repo)
                underName = underlyingCtx.nameInBranch
                if not underName:
                    underName = "/HEAD"

                return (
                    underlyingCtx.renderSubjectAndAuthor() +
                    '&nbsp;<a class="badge badge-info" data-toggle="tooltip" title="{title}" href="{url}">{icon}</a>&nbsp;'
                        .format(
                            url=underlyingCtx.urlString(), 
                            icon=octicon("pin"),
                            title="This commit is a pin update. The message shown here is from " + 
                                "commit %s which is underlying commit %s/%s%s" % (hash[:10], repo, branch, underName)
                            ) 
                    )
            else:
                logging.warn("Couldn't find pinned commit %s/%s/%s", repo, branch, hash)

        text = self.commit.data.subject
        text = text if len(text) <= maxChars else text[:maxChars] + '...'

        return (
            cgi.escape(text) +
            '&nbsp;&middot;&nbsp;<span class="text-muted">by</span> <span class="text-secondary">%s</span>' % self.commit.data.author +
            "&nbsp;&middot;&nbsp;" + 
            self.recency() + 
            self.renderContentCallout() + 
            self.renderLinkToSCM()
            )

    def renderLinkWithSubject(self, maxChars=40, noIcon=False):
        if not self.commit.data:
            return ""

        return (
            self.renderLinkWithShaHash(noIcon=noIcon) + 
            "&nbsp;" +
            self.renderSubjectAndAuthor(maxChars)
            )

    def commitMessageDetail(self, renderParents=False):
        if renderParents:
            parentCommitUrls = ['<span class="mx-2">%s</span>' % self.contextFor(x).renderLinkWithSubject().render() for x in self.commit.data.parents]

            if not parentCommitUrls:
                parent_commits = "None"
            else:
                parent_commits = '<ul style="list-style:none">%s</ul>' % ("".join("<li>%s</li>" % c for c in parentCommitUrls))

            parents = "\n" + "Parent Commits: </pre>" + parent_commits + '<pre style="white-space:pre-wrap">'
        else:
            parents = ""

        return textwrap.dedent("""
            <pre style="white-space: pre-wrap; margin-bottom:0px">commit <b>{commit_hash}</b>
            Author: {author} &lt;{author_email}&gt;
            Date:   {timestamp}{parents}

            {body}
            </pre>
            """).format(
                commit_hash=self.commit.hash,
                body="\n".join(["    " + x for x in cgi.escape(self.commit.data.commitMessage).split("\n")]),
                parents=parents,
                author=self.commit.data.author, 
                author_email=self.commit.data.authorEmail,
                timestamp=time.asctime(time.gmtime(self.commit.data.timestamp))
                )

    def renderContentCallout(self):
        detail_header = "Commit Info"

        detail = self.commitMessageDetail()

        return HtmlGeneration.popover(
            contents=octicon("comment"), 
            detail_title=detail_header, 
            detail_view=detail, width=600, 
            data_placement="right"
            )


    def renderLink(self, includeRepo=True, includeBranch=True):
        res = ""
        if includeRepo:
            assert includeBranch
            res += self.contextFor(self.repo).renderLink()

        if includeBranch:
            if res:
                res += "/"
            res += self.contextFor(self.branch).renderLink(includeRepo=False)

        name = self.nameInBranch

        if not includeRepo and not includeBranch:
            name = "HEAD" + name
        elif not name:
            name = "/HEAD"
        else:
            if len(name) < 5:
                name += "&nbsp;" * max(0, 5 - len(name))

        return res + HtmlGeneration.link(name, self.urlString())

    def primaryObject(self):
        return self.commit

    def urlBase(self):
        return "repos/" + self.reponame + "/-/commits/" + self.commitHash

    def renderPageBody(self):
        view = self.currentView()

        if view == "commit_data":
            return self.renderCommitDataView()
        if view == "test_definitions":
            return self.renderCommitTestDefinitionsInfo()
        if view == "test_suites":
            return self.renderTestSuitesSummary()
        if view == "test_builds":
            return self.renderTestSuitesSummary(builds=True)
        if view == "test_results":
            return self.renderTestResultsGrid()

        return card('Unknown view &quot;<span class="font-weight-bold">%s</span>&quot;' % view)

    def contextViews(self):
        return ["test_results", "test_builds", "test_suites", "commit_data", "test_definitions"]

    def renderViewMenuItem(self, view):
        if view == "commit_data":
            return "Commit Summary"
        if view == "test_definitions":
            return "Test Definitions"
        if view == "test_results":
            return "Test Results"
        if view == "test_suites":
            return "Suites"
        if view == "test_builds":
            return "Builds"
        return view

    def renderViewMenuMouseoverText(self, view):
        if view == "commit_data":
            return "Commit message and author information"
        if view == "test_definitions":
            return "A view of the actual test definitions file used by the looper"
        if view == "test_results":
            return "Test results by configuration"
        if view == "test_suites":
            return "Individual test suites defined by the test definitions"
        if view == "test_builds":
            return "Individual builds defined by the test definitions"
        return view


    def renderCommitDataView(self):
        if not self.commit.data:
            return card("Commit hasn't been imported yet")

        return card(self.commitMessageDetail(renderParents=True))

    def individualTests(self, test):
        res = {}    

        prefix = self.options.get("testGroup","")

        for run in self.database.TestRun.lookupAll(test=test):
            if run.testNames:
                testNames = run.testNames.test_names
                testHasLogs = run.testHasLogs

                for i in xrange(len(run.testNames.test_names)):
                    if testNames[i].startswith(prefix):
                        cur_runs, cur_successes, hasLogs = res.get(testNames[i], (0,0,False))

                        cur_runs += 1
                        cur_successes += 1 if run.testFailures[i] else 0
                        if testHasLogs[i]:
                            hasLogs = True

                        res[run.testNames.test_names[i]] = (cur_runs, cur_successes, hasLogs)
        
        return res

    def allTests(self):
        return self.testManager.database.Test.lookupAll(commitData=self.commit.data)

    def renderTestResultsGrid(self):
        def testFun(c):
            for t in self.testManager.database.Test.lookupAll(commitData=self.commit.data):
                if c == self.testManager.configurationForTest(t) and t.testDefinition.matches.Test:
                    yield t

        configs = set()
        for t in self.database.Test.lookupAll(commitData=self.commit.data):
            if t.testDefinition.matches.Test:
                configs.add(self.testManager.configurationForTest(t))

        if not configs:
            return card("No tests defined.")

        renderer = IndividualTestGridRenderer.IndividualTestGridRenderer(
            sorted(configs),
            self, 
            testFun,
            lambda testGroup, row:
                self.contextFor(
                    ComboContexts.CommitAndConfiguration(self.commit, row)
                    ).withOptions(testGroup=testGroup).urlString(),
            displayIndividualTestsGraphically=False,
            breakOutIndividualTests=self.options.get("testGroup","") != ""
            )

        grid = [[""] + renderer.headers()]

        for configuration in sorted(configs):
            grid.append([
                self.contextFor(
                    ComboContexts.CommitAndConfiguration(self.commit, configuration)
                    ).withOptions(**self.options).renderLink()
                ] + renderer.gridRow(configuration))

        grid = HtmlGeneration.transposeGrid(grid)

        return HtmlGeneration.grid(grid, rowHeightOverride=36)

    def renderCommitTestDefinitionsInfo(self):
        raw_text, extension = self.testManager.getRawTestFileForCommit(self.commit)

        if raw_text:
            return card('<pre class="language-yaml"><code class="line-numbers">%s</code></pre>' % cgi.escape(raw_text))
        else:
            return card("No test definitions found")

    def renderTestSuitesSummary(self, builds=False):
        commit = self.commit

        tests = self.allTests()

        if builds:
            tests = [t for t in tests if t.testDefinition.matches.Build]
        else:
            tests = [t for t in tests if t.testDefinition.matches.Test]
        
        if not tests:
            if commit.data.noTestsFound:
                return card("Commit defined no test definition file.")

            raw_text, extension = self.testManager.getRawTestFileForCommit(commit)
            if not raw_text:
                return card("Commit defined no tests because the test-definitions file is empty.")
            elif commit.data.testDefinitionsError:
                return card("<div>Commit defined no tests or builds. Maybe look at the test definitions? Error was</div><pre><code>%s</code></pre>" % commit.data.testDefinitionsError)
            else:
                return card("Commit defined no %s." % ("builds" if builds else "tests") )

        tests = sorted(tests, key=lambda test: test.fullname)
        
        grid = [["BUILD" if builds else "SUITE", "", "", "ENVIRONMENT", "RUNNING", "COMPLETED", "FAILED", "PRIORITY", "AVG_TEST_CT", "AVG_FAILURE_CT", "AVG_RUNTIME", "", "TEST_DEPS"]]

        for t in tests:
            row = []

            row.append(
                self.contextFor(t).renderLink(includeCommit=False)
                )
            row.append("") #self.clearTestLink(t.fullname))
            row.append(
                HtmlGeneration.Link(self.contextFor(t).bootTestOrEnvUrl(),
                   "BOOT",
                   is_button=True,
                   new_tab=True,
                   button_style=self.renderer.disable_if_cant_write('btn-primary btn-xs')
                   )
                )

            row.append(t.testDefinition.environment_name)

            row.append(str(t.activeRuns))
            row.append(str(t.totalRuns))
            row.append(str(t.totalRuns - t.successes))

            def stringifyPriority(calculatedPriority, priority):
                if priority.matches.HardwareComboUnbootable:
                    return "HardwareComboUnbootable"
                if priority.matches.WaitingOnBuilds:
                    return "WaitingOnBuilds"
                if priority.matches.UnresolvedDependencies:
                    return "UnresolvedTestDependencies"
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
                    row.append(HtmlGeneration.secondsUpToString(sum([testRun.endTimestamp - testRun.startedTimestamp for testRun in finished_tests]) / len(finished_tests)))
                else:
                    row.append("")
            else:
                row.append("")
                row.append("")
                
                if all_noncanceled_tests:
                    row.append(HtmlGeneration.secondsUpToString(sum([time.time() - testRun.startedTimestamp for testRun in all_noncanceled_tests]) / len(all_noncanceled_tests)) + " so far")
                else:
                    row.append("")


            runButtons = []

            for testRun in all_noncanceled_tests:
                runButtons.append(self.renderer.testLogsButton(testRun._identity).render())

            row.append(" ".join(runButtons))
            row.append(self.testDependencySummary(t))

            grid.append(row)

        return HtmlGeneration.grid(grid)
    
    def testDependencySummary(self, t):
        """Return a single cell displaying all the builds this test depends on"""
        return TestSummaryRenderer.TestSummaryRenderer(
            self.testManager.allTestsDependedOnByTest(t),
            ""
            ).renderSummary()


    def childContexts(self, currentChild):
        if isinstance(currentChild.primaryObject(), ComboContexts.CommitAndConfiguration):
            return [self.contextFor(
                ComboContexts.CommitAndConfiguration(commit=self.commit, configurationName=g)
                )
                    for g in sorted(set([self.testManager.configurationForTest(t)
                            for t in self.database.Test.lookupAll(commitData=self.commit.data)
                        ]))
                ]
        if isinstance(currentChild.primaryObject(), self.database.Test):
            if currentChild.primaryObject().testDefinition.matches.Build:
                return [self.contextFor(t)
                        for t in sorted(
                            self.database.Test.lookupAll(commitData=self.commit.data),
                            key=lambda t:t.testDefinition.name
                            ) if t.testDefinition.matches.Build
                        ]
            if currentChild.primaryObject().testDefinition.matches.Test:
                return [self.contextFor(t)
                        for t in sorted(
                            self.database.Test.lookupAll(commitData=self.commit.data),
                            key=lambda t:t.testDefinition.name
                            ) if t.testDefinition.matches.Test
                        ]
        
        return []

    def parentContext(self):
        branch, name = self.testManager.bestCommitBranchAndName(self.commit)

        if branch:
            return self.contextFor(branch)

        return self.contextFor(self.commit.repo)

    def renderMenuItemText(self, isHeader):
        if self.branch:
            name = "HEAD" + self.nameInBranch

            return (octicon("git-commit") if isHeader else "") + name

        return (octicon("git-commit") if isHeader else "") + self.commit.hash[:10]

    def renderPostViewSelector(self):
        tests = self.testManager.database.Test.lookupAll(commitData=self.commit.data)
        all_tests = [x for x in tests if x.testDefinition.matches.Test]
        all_builds = [x for x in tests if x.testDefinition.matches.Build]

        return (
            "Testing:&nbsp;" +
            self.toggleCommitUnderTestLink().render() + 
            "&nbsp;&nbsp;Builds:&nbsp;&nbsp;" + 
            TestSummaryRenderer.TestSummaryRenderer(all_builds, "").renderSummary() +
            "&nbsp;&nbsp;Tests:&nbsp;" +
            TestSummaryRenderer.TestSummaryRenderer(all_tests, "").renderSummary() 
            )
