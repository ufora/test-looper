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
    def __init__(self, renderer, commit, configFilter, projectFilter, options):
        Context.Context.__init__(self, renderer, options)
        self.reponame = commit.repo.name
        self.commitHash = commit.hash
        self.options = options

        self.repo = commit.repo
        self.commit = commit
        self._nameInBranch = None
        self._branch = None

        self.configFilter = configFilter
        self.projectFilter = projectFilter

    def commitsToRender(self):
        return 10

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

    def renderMenuItemText(self, isHeader):
        return (octicon(self.appropriateIcon()) if isHeader else "") + self.appropriateLinkName()

    def appropriateIcon(self):
        if self.configFilter:
            icon = "database"
        elif self.projectFilter:
            icon = "circuit-board"
        else:
            icon = "git-commit"
        return icon

    def appropriateLinkName(self):
        if self.configFilter:
            return self.configFilter
        
        if self.projectFilter:
            return self.projectFilter
        
        if self.branch:
            return "HEAD" + self.nameInBranch

        return self.commit.hash[:8]

    def isPinUpdateCommit(self):
        if not self.commit.data.commitMessage.startwith("Updating pin"):
            return False
    
    def consumePath(self, path):
        if path and path[0] == "configurations":
            groupPath, remainder = self.popToDash(path[1:])

            if not path:
                return None, path

            configurationName = "/".join(groupPath)

            return self.contextFor(ComboContexts.CommitAndFilter(self.commit, configurationName, self.projectFilter)), remainder

        if path and path[0] == "projects":
            groupPath, remainder = self.popToDash(path[1:])

            if not path:
                return None, path

            projectName = "/".join(groupPath)

            return self.contextFor(ComboContexts.CommitAndFilter(self.commit, self.configFilter, projectName)), remainder

        if path and path[0] == "tests":
            testpath, remainder = self.popToDash(path[1:])

            testname = "/".join(testpath)

            if testname in self.commit.data.tests:
                test = self.commit.data.tests[testname]
            else:
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
    
    def renderNavbarLink(self, textOverride=None):
        if textOverride is None:
            textOverride = self.appropriateLinkName()

        return octicon(self.appropriateIcon()) + self.renderLink(includeBranch=False, includeRepo=False, textOverride=textOverride)

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
            def render(x):
                if isinstance(x,str):
                    return x
                return x.render()
            parentCommitUrls = ['<span class="mx-2">%s</span>' % render(self.contextFor(x).renderLinkWithSubject()) for x in self.commit.data.parents]

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


    def renderLink(self, includeRepo=True, includeBranch=True, textOverride=None):
        res = ""
        if includeRepo:
            assert includeBranch
            res += self.contextFor(self.repo).renderLink()

        if includeBranch and not self.branch:
            name = self.commit.hash[:10]
        else:
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

        return (res if not textOverride else "") + HtmlGeneration.link(textOverride or name, self.urlString())

    def primaryObject(self):
        if not (self.configFilter or self.projectFilter):
            return self.commit
        else:
            return ComboContexts.CommitAndFilter(self.commit, self.configFilter, self.projectFilter)

    def urlBase(self):
        res = "repos/" + self.reponame + "/-/commits/" + self.commitHash

        if self.configFilter:
            res += "/configurations/" + self.configFilter

        if self.projectFilter:
            if self.configFilter:
                res += "/-"

            res += "/projects/" + self.projectFilter

        return res

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
            self.testManager._triggerCommitDataUpdate(self.commit)
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
        return self.testManager.allTestsForCommit(self.commit)

    def shouldIncludeTest(self, test):
        if self.projectFilter and test.testDefinitionSummary.project != self.projectFilter:
            return False
        if self.configFilter and test.testDefinitionSummary.configuration != self.configFilter:
            return False
        return True

    def renderProjectAndFilterCrossGrid(self):
        projects = set()
        configurations = set()

        for t in self.allTests():
            if t.testDefinitionSummary.type == "Test" and self.shouldIncludeTest(t):
                projects.add(t.testDefinitionSummary.project)
                configurations.add(t.testDefinitionSummary.configuration)

        renderer = TestGridRenderer.TestGridRenderer(
            sorted(projects), 
            lambda p: [t for t in self.allTests() if t.testDefinitionSummary.project == p],
            lambda group: self.contextFor(ComboContexts.CommitAndFilter(self.commit, group, "")).renderNavbarLink(textOverride=group),
            lambda group, row: self.contextFor(ComboContexts.CommitAndFilter(self.commit, group, row)).urlString(),
            lambda test: test.testDefinitionSummary.configuration
            )

        grid = [["PROJECT"] + renderer.headers()]

        for p in sorted(projects):
            gridrow = renderer.gridRow(p)

            grid.append([
                self.contextFor(ComboContexts.CommitAndFilter(self.commit, "", p)).renderLink(textOverride=p)
                ] + gridrow)

        return HtmlGeneration.grid(grid)

    def renderProjectAndFilterCrossGridOverCommits(self, configFilter):
        projects = set()

        for t in self.allTests():
            if t.testDefinitionSummary.type == "Test" and self.shouldIncludeTest(t):
                projects.add(t.testDefinitionSummary.project)

        commits = [self.commit]
        while len(commits) < self.commitsToRender() and commits[-1].data and commits[-1].data.parents:
            commits.append(commits[-1].data.parents[-1])

        grid = []
        renderers = []
        for c in commits:
            def makeRenderer(commit):
                return TestGridRenderer.TestGridRenderer(
                    sorted(projects),
                    lambda p: [t for t in self.allTests() if t.testDefinitionSummary.project == p and t.testDefinitionSummary.configuration == configFilter],
                    lambda group: self.contextFor(ComboContexts.CommitAndFilter(commit, configFilter, group)).renderLink(textOverride=group,includeRepo=False, includeBranch=False),
                    lambda group, row: self.contextFor(ComboContexts.CommitAndFilter(commit, group, row)).urlString(),
                    lambda test: ""
                    )

            renderers.append(makeRenderer(c))

        grid = [[""] + [renderer.headers()[0] for renderer in renderers]]

        for project in sorted(projects):
            gridrow = [renderer.gridRow(project)[0] for renderer in renderers]

            grid.append([
                self.contextFor(ComboContexts.CommitAndFilter(self.commit, configFilter, project)).renderLink(textOverride=project)
                ] + gridrow)

        return HtmlGeneration.grid(grid)

    def renderTestResultsGrid(self):
        projectFilter = self.projectFilter
        configFilter = self.configFilter

        projects = set()
        configurations = set()

        for t in self.allTests():
            if self.shouldIncludeTest(t):
                projects.add(t.testDefinitionSummary.project)
                configurations.add(t.testDefinitionSummary.configuration)

        if not projectFilter and len(projects) == 1:
            projectFilter = list(projects)[0]

        if not configFilter and len(configurations) == 1:
            configFilter = list(configurations)[0]

        if not (projectFilter or configFilter):
            return self.renderProjectAndFilterCrossGrid()

        if configFilter and not projectFilter:
            return self.renderProjectAndFilterCrossGridOverCommits(configFilter)

        if not configurations or not projects:
            return card("No tests defined.")

        if configFilter:
            #show broken out tests over the last N commits
            rows = [self.commit]
            while len(rows) < self.commitsToRender() and rows[-1].data and rows[-1].data.parents:
                rows.append(rows[-1].data.parents[-1])

            def rowLinkFun(row):
                return self.contextFor(
                    ComboContexts.CommitAndFilter(row, configFilter, projectFilter)
                    ).withOptions(**self.options).renderLink(includeRepo=False, includeBranch=False)

            def testFun(row):
                for t in self.testManager.allTestsForCommit(row):
                    if self.shouldIncludeTest(t) and t.testDefinitionSummary.type == "Test":
                        yield t

            def cellUrlFun(testGroup, row):
                return self.contextFor(
                    ComboContexts.CommitAndFilter(row, configFilter, projectFilter)
                    ).withOptions(**self.options).withOptions(testGroup=testGroup).urlString()
        else:
            #show tests over configurations
            rows = sorted(configurations)

            def rowLinkFun(row):
                return self.contextFor(
                    ComboContexts.CommitAndFilter(self.commit, row, projectFilter)
                    ).withOptions(**self.options).renderNavbarLink(textOverride=row)

            def testFun(row):
                for t in self.testManager.allTestsForCommit(self.commit):
                    if self.shouldIncludeTest(t) and t.testDefinitionSummary.type == "Test" and t.testDefinitionSummary.configuration == row:
                        yield t

            def cellUrlFun(testGroup, row):
                return self.contextFor(
                    ComboContexts.CommitAndFilter(self.commit, row, projectFilter)
                    ).withOptions(**self.options).withOptions(testGroup=testGroup).urlString()

        renderer = IndividualTestGridRenderer.IndividualTestGridRenderer(
            rows,
            self, 
            testFun,
            cellUrlFun,
            breakOutIndividualTests=True#self.options.get("testGroup","") != ""
            )

        grid = [[""] + renderer.headers()]

        for row in rows:
            grid.append([rowLinkFun(row)] + renderer.gridRow(row))

        grid = HtmlGeneration.transposeGrid(grid)

        return HtmlGeneration.grid(grid)

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
            tests = [t for t in tests if t.testDefinitionSummary.type == "Build" and self.shouldIncludeTest(t)]
        else:
            tests = [t for t in tests if t.testDefinitionSummary.type == "Test" and self.shouldIncludeTest(t)]
        
        if not tests:
            if commit.data.noTestsFound:
                return card("Commit defined no test definition file.")

            raw_text, extension = self.testManager.getRawTestFileForCommit(commit)
            if not raw_text:
                return card("Commit defined no tests because the test-definitions file is empty.")
            elif commit.data.testDefinitionsError:
                return card("<div>Commit defined no tests or builds. Maybe look at the test definitions? Error was</div><pre><code>%s</code></pre>" % commit.data.testDefinitionsError)
            else:
                if self.projectFilter and self.configFilter:
                    return card("Commit defined no %s for project '%s' and configuration '%s'." % ("builds" if builds else "tests", self.projectFilter, self.configFilter ))
                if self.projectFilter:
                    return card("Commit defined no %s for project '%s'." % ("builds" if builds else "tests", self.projectFilter ))
                if self.configFilter:
                    return card("Commit defined no %s for configuration %s." % ("builds" if builds else "tests", self.configFilter ))
                return card("Commit defined no %s." % ("builds" if builds else "tests") )

        tests = sorted(tests, key=lambda test: test.testDefinitionSummary.name)
        
        if builds:
            grid = [["BUILD", "HASH", "", "PROJECT", "CONFIGURATION", "STATUS", "RUNS", "RUNTIME", "", "DEPENDENCIES"]]
        else:
            grid = [["SUITE", "HASH", "", "PROJECT", "CONFIGURATION", "STATUS", "RUNS", "TEST_CT", "FAILURE_CT", "AVG_RUNTIME", "", "DEPENDENCIES"]]

        for t in tests:
            row = []

            row.append(
                self.contextFor(t).renderLink(includeCommit=False)
                )
            row.append(t.hash[:8])
            row.append(
                HtmlGeneration.Link(self.contextFor(t).bootTestOrEnvUrl(),
                   "BOOT",
                   is_button=True,
                   new_tab=True,
                   button_style=self.renderer.disable_if_cant_write('btn-primary btn-xs')
                   )
                )

            row.append(t.testDefinitionSummary.project)
            row.append(t.testDefinitionSummary.configuration)

            row.append(TestSummaryRenderer.TestSummaryRenderer([t],"", ignoreIndividualTests=True).renderSummary())
            row.append(str(t.totalRuns))

            all_tests = list(self.testManager.database.TestRun.lookupAll(test=t))
            all_noncanceled_tests = [testRun for testRun in all_tests if not testRun.canceled]
            finished_tests = [testRun for testRun in all_noncanceled_tests if testRun.endTimestamp > 0.0]

            if t.totalRuns:
                if not builds:
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
                if not builds:
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
        if isinstance(currentChild.primaryObject(), ComboContexts.CommitAndFilter):
            if currentChild.configFilter and currentChild.projectFilter:
                return [self.contextFor(
                    ComboContexts.CommitAndFilter(commit=self.commit, configurationName=g, projectName=self.projectFilter)
                    )
                        for g in sorted(set([t.testDefinitionSummary.configuration
                                for t in self.testManager.allTestsForCommit(self.commit) 
                                    if t.testDefinitionSummary.project == self.projectFilter
                            ]))
                    ]
            if currentChild.configFilter:
                return [self.contextFor(
                    ComboContexts.CommitAndFilter(commit=self.commit, configurationName=g, projectName="")
                    )
                        for g in sorted(set([t.testDefinitionSummary.configuration
                                for t in self.testManager.allTestsForCommit(self.commit) 
                            ]))
                    ]
            else:
                return [self.contextFor(
                    ComboContexts.CommitAndFilter(commit=self.commit, configurationName="", projectName=g)
                    )
                        for g in sorted(set([t.testDefinitionSummary.project
                                for t in self.testManager.allTestsForCommit(self.commit)
                            ]))
                    ]
        if isinstance(currentChild.primaryObject(), self.database.Test):
            if currentChild.primaryObject().testDefinitionSummary.type == 'Build':
                return [self.contextFor(t)
                        for t in sorted(
                            self.allTests(),
                            key=lambda t:t.testDefinitionSummary.name
                            ) if t.testDefinitionSummary.type == "Build"
                        ]
            if currentChild.primaryObject().testDefinitionSummary.type == 'Test':
                return [self.contextFor(t)
                        for t in sorted(
                            self.allTests(),
                            key=lambda t:t.testDefinitionSummary.name
                            ) if t.testDefinitionSummary.type == "Test"
                        ]
        
        return []

    def parentContext(self):
        if self.projectFilter and self.configFilter:
            return self.contextFor(
                ComboContexts.CommitAndFilter(self.commit, "", self.projectFilter)
                ).withOptions(**self.options)

        if self.configFilter or self.projectFilter:
            return self.contextFor(
                ComboContexts.CommitAndFilter(self.commit, "", "")
                ).withOptions(**self.options)
        
        branch, name = self.testManager.bestCommitBranchAndName(self.commit)

        if branch:
            return self.contextFor(branch)

        return self.contextFor(self.commit.repo)

    def renderMenuItemText(self, isHeader):
        return (octicon(self.appropriateIcon()) if isHeader else "") + self.appropriateLinkName()

    def renderPostViewSelector(self):
        tests = self.allTests()
        all_tests = [x for x in tests if x.testDefinitionSummary.type == "Test"]
        all_builds = [x for x in tests if x.testDefinitionSummary.type == "Build"]

        return (
            "Testing:&nbsp;" +
            self.toggleCommitUnderTestLink().render() + 
            "&nbsp;&nbsp;Builds:&nbsp;&nbsp;" + 
            TestSummaryRenderer.TestSummaryRenderer(all_builds, "").renderSummary() +
            "&nbsp;&nbsp;Tests:&nbsp;" +
            TestSummaryRenderer.TestSummaryRenderer(all_tests, "").renderSummary() 
            )
