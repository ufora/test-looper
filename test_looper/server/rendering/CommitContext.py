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

ENABLE_BOOT_BUTTONS = False


class CommitContext(Context.Context):

    TARGET_COUNT_OPTIONS = [0, 1, 5, 10, 20, 50, 100]

    def __init__(
        self, renderer, commit, configFilter, projectFilter, parentLevel, options
    ):
        Context.Context.__init__(self, renderer, options)
        self.reponame = commit.repo.name
        self.commitHash = commit.hash
        self.options = options
        self.parentLevel = parentLevel

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
            self._branch, self._nameInBranch = self.testManager.bestCommitBranchAndName(
                self.commit
            )
        return self._branch

    @property
    def nameInBranch(self):
        if self._branch is None:
            self._branch, self._nameInBranch = self.testManager.bestCommitBranchAndName(
                self.commit
            )
        return self._nameInBranch

    def renderMenuItemText(self, isHeader):
        return (
            octicon(self.appropriateIcon()) if isHeader else ""
        ) + self.appropriateLinkName()

    def appropriateIcon(self):
        if self.parentLevel == 0:
            return "database"
        if self.parentLevel == 1:
            return "circuit-board"
        return "git-commit"

    def appropriateLinkName(self):
        if self.parentLevel == 0:
            return self.configFilter or '<span class="text-muted">all configs</span>'

        if self.parentLevel == 1:
            return self.projectFilter or '<span class="text-muted">all projects</span>'

        if self.branch:
            return "HEAD" + self.nameInBranch

        return self.commit.hash[:8]

    def isPinUpdateCommit(self):
        if not self.commit.data.commitMessage.startwith("Updating pin"):
            return False

    def consumePath(self, path):
        while path and path[0] == "-":
            path = path[1:]

        if path and path[0] == "individualTest":
            return (
                self.contextFor(
                    ComboContexts.IndividualTest(
                        self.primaryObject(), "/".join(path[1:])
                    )
                ),
                [],
            )

        if path and path[0] == "configurations":
            groupPath, remainder = self.popToDash(path[1:])

            if not path:
                return None, path

            configurationName = "/".join(groupPath)

            return (
                self.contextFor(
                    ComboContexts.CommitAndFilter(
                        self.commit, configurationName, self.projectFilter
                    )
                ),
                remainder,
            )

        if path and path[0] == "projects":
            groupPath, remainder = self.popToDash(path[1:])

            if not path:
                return None, path

            projectName = "/".join(groupPath)

            return (
                self.contextFor(
                    ComboContexts.CommitAndFilter(
                        self.commit, self.configFilter, projectName
                    )
                ),
                remainder,
            )

        if path and path[0] == "tests":
            testpath, remainder = self.popToDash(path[1:])

            testname = "/".join(testpath)

            if testname in self.commit.data.tests:
                test = self.commit.data.tests[testname]
            else:
                return None, path

            return self.renderer.contextFor(test, self.options), remainder

        return None, path

    def dropdownForTestPrioritization(self):
        commit = self.commit

        if not commit.data:
            return ""

        if len(commit.userEnabledTestSets):
            elt = "testing " + ", ".join(sorted(commit.userEnabledTestSets))
        else:
            elt = '<span class="text-muted">not testing</span>'

        menu_items = []

        sortedTestSets = sorted(commit.data.testSets)
        if "all" in sortedTestSets:
            sortedTestSets.remove("all")
            sortedTestSets = ["all"] + sortedTestSets

        for test_set in sortedTestSets:
            isEnabledNow = test_set in commit.userEnabledTestSets

            menu_items.append(
                '<a class="dropdown-item {active}" href="{link}">{contents}</a>'.format(
                    link=self.withOptions(
                        action="toggle_tests_on",
                        test_set=test_set,
                        redirect=self.renderer.redirect(),
                    ).urlString(),
                    contents=test_set,
                    active="active" if isEnabledNow else "",
                )
            )

        return """
            <div class="btn-group">
              <a role="button" class="btn btn-xs {btnstyle}" title="{title}">{elt}</a>
              <button class="btn btn-xs {btnstyle} dropdown-toggle dropdown-toggle-split" type="button" id="dropdownMenuButton" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false">
              </button>
              <div class="dropdown-menu" aria-labelledby="dropdownMenuButton">
                {dd_items}
              </div>
              
            </div>
            """.format(
            elt=elt,
            title="Test subsets we want to run",
            dd_items="".join(menu_items),
            btnstyle="btn-outline-secondary",
        )

    def renderLinkToSCM(self, big=False):
        url = self.renderer.src_ctrl.commit_url(self.commit.repo.name, self.commit.hash)
        name = ""
        if big and url and "github" in url:
            name = "github"
        elif big and url and "gitlab" in url:
            name = "gitlab"
        elif big:
            name = "source"

        return HtmlGeneration.link(octicon("diff") + name, url, hover_text="View diff")

    def renderNavbarLink(self, textOverride=None):
        if textOverride is None:
            textOverride = self.appropriateLinkName()

        return octicon(self.appropriateIcon()) + self.renderLink(
            includeBranch=False, includeRepo=False, textOverride=textOverride
        )

    def recency(self):
        return '<span class="text-muted">%s</span>' % (
            HtmlGeneration.secondsUpToString(time.time() - self.commit.data.timestamp)
            + " ago"
        )

    def renderLinkWithShaHash(self, noIcon=False):
        if not self.commit.data:
            return ""

        return (octicon("git-commit") if not noIcon else "") + HtmlGeneration.link(
            "<code>" + self.commit.hash[:8] + "</code>",
            self.urlString(),
            hover_text=(
                "commit "
                + self.commit.hash[:10]
                + " : "
                + ("" if not self.commit.data else self.commit.data.commitMessage)
            ),
        )

    def unpackCommitPin(self, commit):
        if not commit or not commit.data:
            return None

        pinUpdate = BranchPinning.unpackCommitPinUpdateMessage(
            commit.data.commitMessage
        )

        if pinUpdate:
            repo, branch, hash, refname = pinUpdate
            underlying_commit = self.testManager._lookupCommitByHash(
                repo, hash, create=False
            )

            if underlying_commit and underlying_commit.data:
                subpin = self.unpackCommitPin(underlying_commit)
                if subpin:
                    return subpin
                return pinUpdate

    def renderSubjectAndAuthor(self, maxChars=40):
        if not self.commit.data:
            return ""

        pinUpdate = self.unpackCommitPin(self.commit)

        if pinUpdate:
            repo, branch, hash, repodef_name = pinUpdate
            underlying_commit = self.testManager._lookupCommitByHash(
                repo, hash, create=False
            )

            if underlying_commit and underlying_commit.data:
                underlyingCtx = self.contextFor(underlying_commit)
                underRepo = self.contextFor(underlying_commit.repo)
                underName = underlyingCtx.nameInBranch
                if not underName:
                    underName = "/HEAD"

                return (
                    '<a class="badge badge-info" data-toggle="tooltip" title="{title}" href="{url}">{repo}</a>&nbsp;'.format(
                        repo=self.renderer.repoDisplayName(underlying_commit.repo.name),
                        url=underlyingCtx.urlString(),
                        title="This commit is a pin update. The message shown here is from "
                        + "commit %s which is underlying commit %s/%s%s"
                        % (hash[:10], repo, branch, underName),
                    )
                    + "&nbsp;"
                    + underlyingCtx.renderSubjectAndAuthor()
                )
            else:
                logging.warn("Couldn't find pinned commit %s/%s/%s", repo, branch, hash)

        text = self.commit.data.subject
        text = text if len(text) <= maxChars else text[:maxChars] + "..."

        return (
            cgi.escape(text)
            + '&nbsp;&middot;&nbsp;<span class="text-muted">by</span> <span class="text-secondary">%s</span>'
            % self.commit.data.author
            + "&nbsp;&middot;&nbsp;"
            + self.recency()
            + self.renderContentCallout()
            + self.renderLinkToSCM()
        )

    def renderLinkWithSubject(self, maxChars=40, noIcon=False):
        if not self.commit.data:
            return ""

        return (
            self.renderLinkWithShaHash(noIcon=noIcon)
            + "&nbsp;"
            + self.renderSubjectAndAuthor(maxChars)
        )

    def commitMessageDetail(self):
        return textwrap.dedent(
            """
            <pre style="white-space: pre-wrap; margin-bottom:0px">commit <b>{commit_hash}</b>
            Author: {author} &lt;{author_email}&gt;
            Date:   {timestamp}

            {body}
            </pre>
            """
        ).format(
            commit_hash=self.commit.hash,
            body="\n".join(
                [
                    "    " + x
                    for x in cgi.escape(self.commit.data.commitMessage).split("\n")
                ]
            ),
            author=self.commit.data.author,
            author_email=self.commit.data.authorEmail,
            timestamp=time.asctime(time.gmtime(self.commit.data.timestamp)),
        )

    def renderContentCallout(self):
        detail_header = "Commit Info"

        detail = self.commitMessageDetail()

        return HtmlGeneration.popover(
            contents=octicon("comment"),
            detail_title=detail_header,
            detail_view=detail,
            width=600,
            data_placement="right",
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

        hover_text = (
            cgi.escape(self.commit.data.commitMessage) if self.commit.data else None
        )

        return (res if not textOverride else "") + HtmlGeneration.link(
            textOverride or name, self.urlString(), hover_text=hover_text
        )

    def primaryObject(self):
        return ComboContexts.CommitAndFilter(
            self.commit, self.configFilter, self.projectFilter, self.parentLevel
        )

    def urlBase(self):
        res = "repos/" + self.reponame + "/-/commits/" + self.commitHash

        if self.configFilter:
            res += "/configurations/" + self.configFilter

        if self.projectFilter:
            if self.configFilter:
                res += "/-"

            res += "/projects/" + self.projectFilter

        return res

    def handleAction(self):
        if self.options.get("action", "") == "update_suite_runs":
            suite = self.commit.data.tests[self.options.get("suite")]
            suite.runsDesired = max(0, min(int(self.options.get("targetRuns")), 100))
            self.testManager._triggerTestPriorityUpdate(suite)

        if self.options.get("action", "") == "update_all_suites_runs":
            runsDesired = max(0, min(int(self.options.get("targetRuns")), 100))
            for suite in self.commit.data.tests.values():
                suite.runsDesired = runsDesired
                self.testManager._triggerTestPriorityUpdate(suite)

        if self.options.get("action", "") == "force_reparse":
            self.testManager._forceTriggerCommitTestParse(self.commit)

        if self.options.get("action", "") == "toggle_tests_on":
            test_set = self.options.get("test_set")

            new_sets = list(self.commit.userEnabledTestSets)
            hasIt = test_set in new_sets

            if not hasIt and test_set == "all":
                new_sets = ["all"]
            elif hasIt:
                new_sets.remove(test_set)
            else:
                new_sets.append(test_set)
                if "all" in new_sets:
                    new_sets.remove("all")

            self.testManager._setCommitUserEnabledTestSets(self.commit, new_sets)

    def renderPageBody(self):
        if self.options.get("action", ""):
            self.handleAction()
            return HtmlGeneration.Redirect(
                self.options.get("redirect", "")
                or self.withOptionsReset(view=self.options.get("view")).urlString()
            )

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
        if view == "repo_refs":
            return self.renderRepoReferencesGrid()

        return card(
            'Unknown view &quot;<span class="font-weight-bold">%s</span>&quot;' % view
        )

    def renderRepoReferencesGrid(self):
        lines = [["refname", "Commit", "Target Branch"]]

        if not self.commit.data:
            return card("Commit data not loaded yet.")

        if not self.commit.data.repos:
            return card("Commit has no references to external repos.")

        for refname, repoRef in sorted(self.commit.data.repos.items()):
            if repoRef.matches.Pin:
                lines.append(
                    [
                        refname,
                        self.renderPinReference(refname, repoRef),
                        repoRef.branchname() if repoRef.branchname() else "",
                    ]
                )

        return HtmlGeneration.grid(lines)

    def renderPinReference(self, reference_name, repoRef, includeName=False):
        if includeName:
            preamble = reference_name + "-&gt;"
        else:
            preamble = ""

        repoName = repoRef.reponame()
        commitHash = repoRef.commitHash()

        repo = self.testManager.database.Repo.lookupAny(name=repoName)
        if not repo:
            return preamble + HtmlGeneration.lightGreyWithHover(
                repoRef.reference, "Can't find repo %s" % repoName
            )

        commit = self.testManager.database.Commit.lookupAny(
            repo_and_hash=(repo, commitHash)
        )
        if not commit:
            return preamble + HtmlGeneration.lightGreyWithHover(
                repoRef.reference[:--30], "Can't find commit %s" % commitHash[:10]
            )

        branches = {
            k.branchname: v
            for k, v in self.testManager.commitFindAllBranches(commit).items()
        }

        if repoRef.branch not in branches:
            return preamble + self.contextFor(commit).renderLink()

        return preamble + self.contextFor(commit).renderLink()

    def contextViews(self):
        return [
            "test_results",
            "test_builds",
            "test_suites",
            "commit_data",
            "repo_refs",
            "test_definitions",
        ]

    def renderViewMenuItem(self, view):
        if view == "commit_data":
            return "Commit Summary"
        if view == "repo_refs":
            return "Repo Refs"
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

        return card(self.commitMessageDetail())

    def individualTests(self, test):
        res = {}

        for run in self.database.TestRun.lookupAll(test=test):
            if run.testNames:
                testNames = run.testNames.test_names
                testHasLogs = run.testHasLogs

                for i in range(len(run.testNames.test_names)):
                    cur_runs, cur_successes, hasLogs = res.get(
                        testNames[i], (0, 0, False)
                    )

                    cur_runs += 1
                    cur_successes += 1 if run.testFailures[i] else 0
                    if testHasLogs[i]:
                        hasLogs = True

                    res[run.testNames.test_names[i]] = (
                        cur_runs,
                        cur_successes,
                        hasLogs,
                    )

        return res

    def allTests(self):
        return self.testManager.allTestsForCommit(self.commit)

    def shouldIncludeTest(self, test):
        # if test.testDefinitionSummary.disabled and not self.options.get("show_disabled"):
        #    return False

        if (
            self.projectFilter
            and test.testDefinitionSummary.project != self.projectFilter
        ):
            return False
        if (
            self.configFilter
            and test.testDefinitionSummary.configuration != self.configFilter
        ):
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
            lambda p: [
                t
                for t in self.allTests()
                if t.testDefinitionSummary.project == p and self.shouldIncludeTest(t)
            ],
            lambda group: self.contextFor(
                ComboContexts.CommitAndFilter(self.commit, group, "")
            ).renderNavbarLink(textOverride=group),
            lambda group, row: self.contextFor(
                ComboContexts.CommitAndFilter(self.commit, group, row)
            ).urlString(),
            lambda test: test.testDefinitionSummary.configuration,
        )

        grid = [["PROJECT"] + renderer.headers()]

        for p in sorted(projects):
            gridrow = renderer.gridRow(p)

            grid.append(
                [
                    self.contextFor(
                        ComboContexts.CommitAndFilter(self.commit, "", p)
                    ).renderLink(textOverride=p)
                ]
                + gridrow
            )

        return HtmlGeneration.grid(grid)

    def renderProjectAndFilterCrossGridOverCommits(self, configFilter):
        projects = set()

        for t in self.allTests():
            if t.testDefinitionSummary.type == "Test" and self.shouldIncludeTest(t):
                projects.add(t.testDefinitionSummary.project)

        commits = [self.commit]
        while (
            len(commits) < self.commitsToRender()
            and commits[-1].data
            and commits[-1].data.parents
        ):
            commits.append(commits[-1].data.parents[-1])

        grid = []
        renderers = []
        for c in commits:

            def makeRenderer(commit):
                return TestGridRenderer.TestGridRenderer(
                    sorted(projects),
                    lambda p: [
                        t
                        for t in self.allTests()
                        if self.shouldIncludeTest(t)
                        and t.testDefinitionSummary.project == p
                        and t.testDefinitionSummary.configuration == configFilter
                    ],
                    lambda group: self.contextFor(
                        ComboContexts.CommitAndFilter(commit, configFilter, group)
                    ).renderLink(
                        textOverride=group, includeRepo=False, includeBranch=False
                    ),
                    lambda group, row: self.contextFor(
                        ComboContexts.CommitAndFilter(commit, group, row)
                    ).urlString(),
                    lambda test: "",
                )

            renderers.append(makeRenderer(c))

        grid = [[""] + [renderer.headers()[0] for renderer in renderers]]

        for project in sorted(projects):
            gridrow = [renderer.gridRow(project)[0] for renderer in renderers]

            grid.append(
                [
                    self.contextFor(
                        ComboContexts.CommitAndFilter(
                            self.commit, configFilter, project
                        )
                    ).renderLink(textOverride=project)
                ]
                + gridrow
            )

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
            # show broken out tests over the last N commits
            rows = [self.commit]
            while (
                len(rows) < self.commitsToRender()
                and rows[-1].data
                and rows[-1].data.parents
            ):
                rows.append(rows[-1].data.parents[-1])

            def rowLinkFun(row):
                return (
                    self.contextFor(
                        ComboContexts.CommitAndFilter(row, configFilter, projectFilter)
                    )
                    .withOptions(**self.options)
                    .renderLinkWithShaHash()
                )

            def testFun(row):
                for t in self.testManager.allTestsForCommit(row):
                    if (
                        self.shouldIncludeTest(t)
                        and t.testDefinitionSummary.type == "Test"
                    ):
                        yield t

            def cellUrlFun(testGroup, row):
                return (
                    self.contextFor(
                        ComboContexts.CommitAndFilter(row, configFilter, projectFilter)
                    )
                    .withOptions(**self.options)
                    .withOptions(testGroup=testGroup)
                    .urlString()
                )

            def rowContextFun(row):
                return ComboContexts.CommitAndFilter(row, configFilter, projectFilter)

        else:
            # show tests over configurations
            rows = sorted(configurations)

            def rowLinkFun(row):
                return (
                    self.contextFor(
                        ComboContexts.CommitAndFilter(self.commit, row, projectFilter)
                    )
                    .withOptions(**self.options)
                    .renderNavbarLink(textOverride=row)
                )

            def testFun(row):
                for t in self.testManager.allTestsForCommit(self.commit):
                    if (
                        self.shouldIncludeTest(t)
                        and t.testDefinitionSummary.type == "Test"
                        and t.testDefinitionSummary.configuration == row
                    ):
                        yield t

            def cellUrlFun(testGroup, row):
                return (
                    self.contextFor(
                        ComboContexts.CommitAndFilter(self.commit, row, projectFilter)
                    )
                    .withOptions(**self.options)
                    .withOptions(testGroup=testGroup)
                    .urlString()
                )

            def rowContextFun(row):
                return ComboContexts.CommitAndFilter(self.commit, row, projectFilter)

        renderer = IndividualTestGridRenderer.IndividualTestGridRenderer(
            rows, self, testFun, cellUrlFun, rowContextFun
        )

        grid = [[""] + renderer.headers()]

        for row in rows:
            grid.append([rowLinkFun(row)] + renderer.gridRow(row))

        grid = HtmlGeneration.transposeGrid(grid)

        if len(grid) == 1:
            return card("No test data available yet.")

        return HtmlGeneration.grid(grid, dataTables=True)

    def renderCommitTestDefinitionsInfo(self):
        raw_text, extension = self.testManager.getRawTestFileForCommit(self.commit)

        post_text = HtmlGeneration.Link(
            self.withOptions(action="force_reparse").urlString(),
            "Force Test Reparse",
            is_button=True,
            button_style=self.renderer.disable_if_cant_write("btn-primary btn-xs mt-4"),
        ).render()

        if raw_text:
            return (
                card(
                    '<pre class="language-yaml"><code class="line-numbers">%s</code></pre>'
                    % cgi.escape(raw_text)
                )
                + post_text
            )
        else:
            return card("No test definitions found") + post_text

    def renderTestSuitesSummary(self, builds=False):
        commit = self.commit

        tests = self.allTests()

        if builds:
            tests = [
                t
                for t in tests
                if t.testDefinitionSummary.type == "Build" and self.shouldIncludeTest(t)
            ]
        else:
            tests = [
                t
                for t in tests
                if t.testDefinitionSummary.type == "Test" and self.shouldIncludeTest(t)
            ]

        if not tests:
            if commit.data.noTestsFound:
                return card("Commit defined no test definition file.")

            raw_text, extension = self.testManager.getRawTestFileForCommit(commit)
            if not raw_text:
                return card(
                    "Commit defined no tests because the test-definitions file is empty."
                )
            elif commit.data.testDefinitionsError:
                return card(
                    "<div>Commit defined no tests or builds. Maybe look at the test definitions? Error was</div><pre><code>%s</code></pre>"
                    % commit.data.testDefinitionsError
                )
            else:
                if self.projectFilter and self.configFilter:
                    return card(
                        "Commit defined no %s for project '%s' and configuration '%s'."
                        % (
                            "builds" if builds else "tests",
                            self.projectFilter,
                            self.configFilter,
                        )
                    )
                if self.projectFilter:
                    return card(
                        "Commit defined no %s for project '%s'."
                        % ("builds" if builds else "tests", self.projectFilter)
                    )
                if self.configFilter:
                    return card(
                        "Commit defined no %s for configuration %s."
                        % ("builds" if builds else "tests", self.configFilter)
                    )
                return card("Commit defined no %s." % ("builds" if builds else "tests"))

        tests = sorted(tests, key=lambda test: test.testDefinitionSummary.name)

        if builds:
            grid = [
                [
                    "BUILD",
                    "HASH",
                    "",
                    "PROJECT",
                    "CONFIGURATION",
                    "PRIORITIZED",
                    "STATUS",
                    "STAGE",
                    "RUNS",
                    "RUNTIME",
                    "",
                    "DEPENDENCIES",
                ]
            ]
        else:
            grid = [
                [
                    "SUITE",
                    "HASH",
                    "",
                    "PROJECT",
                    "CONFIGURATION",
                    "PRIORITIZED",
                    "STATUS",
                    "RUNS",
                    "TARGET_RUNS",
                    "TEST_CT",
                    "FAILURE_CT",
                    "AVG_RUNTIME",
                    "",
                    "DEPENDENCIES",
                ]
            ]

        if self.options.get("show_disabled"):
            # grid[0].append("Disabled")
            grid[0].append("Calculated Priority")

        for t in tests:
            row = []

            row.append(self.contextFor(t).renderLink(includeCommit=False))
            row.append(t.hash[:8])

            if ENABLE_BOOT_BUTTONS:
                row.append(
                    HtmlGeneration.Link(
                        self.contextFor(t).bootTestOrEnvUrl(),
                        "BOOT",
                        is_button=True,
                        new_tab=True,
                        button_style=self.renderer.disable_if_cant_write(
                            "btn-primary btn-xs"
                        ),
                    )
                )
            else:
                row.append("")

            row.append(t.testDefinitionSummary.project)
            row.append(t.testDefinitionSummary.configuration)
            row.append(octicon("check") if t.calculatedPriority else "")

            row.append(
                TestSummaryRenderer.TestSummaryRenderer(
                    [t], "", ignoreIndividualTests=True
                ).renderSummary()
            )

            all_tests = list(self.testManager.database.TestRun.lookupAll(test=t))
            all_noncanceled_tests = [
                testRun for testRun in all_tests if not testRun.canceled
            ]
            all_running_tests = [
                testRun
                for testRun in all_noncanceled_tests
                if testRun.endTimestamp == 0.0
            ]
            finished_tests = [
                testRun
                for testRun in all_noncanceled_tests
                if testRun.endTimestamp > 0.0
            ]

            if builds:
                if not all_running_tests or not t.testDefinitionSummary.artifacts:
                    row.append("")
                else:
                    completed = len(all_running_tests[0].artifactsCompleted)
                    row.append(
                        "%s / %s" % (completed, len(t.testDefinitionSummary.artifacts))
                        + (
                            [" (" + x + ")" for x in t.testDefinitionSummary.artifacts]
                            + [""]
                        )[completed]
                    )

            row.append(str(t.totalRuns))

            if not builds:
                row.append(self.renderIncreaseSuiteTargetCount(t))

            if t.totalRuns:
                if not builds:
                    row.append(t.testResultSummary.totalTestCount)
                    row.append(t.testResultSummary.avgFailureRate)

                if finished_tests:
                    row.append(
                        HtmlGeneration.secondsUpToString(
                            sum(
                                [
                                    testRun.endTimestamp - testRun.startedTimestamp
                                    for testRun in finished_tests
                                ]
                            )
                            / len(finished_tests)
                        )
                    )
                else:
                    row.append("")
            else:
                if not builds:
                    row.append("")
                    row.append("")

                if all_noncanceled_tests:
                    row.append(
                        HtmlGeneration.secondsUpToString(
                            sum(
                                [
                                    time.time() - testRun.startedTimestamp
                                    for testRun in all_noncanceled_tests
                                ]
                            )
                            / len(all_noncanceled_tests)
                        )
                        + " so far"
                    )
                else:
                    row.append("")

            runButtons = []

            for testRun in all_noncanceled_tests[:5]:
                runButtons.append(
                    self.renderer.testLogsButton(testRun._identity).render()
                )
            if len(all_noncanceled_tests) > 5:
                runButtons.append(" and %s more" % (len(all_noncanceled_tests) - 5))

            row.append(" ".join(runButtons))
            row.append(self.testDependencySummary(t))

            if self.options.get("show_disabled"):
                # row.append("Disabled" if t.testDefinitionSummary.disabled else "")
                row.append(str(t.calculatedPriority))

            grid.append(row)

        return (
            HtmlGeneration.card(
                "Set Target Runs for all Suites" + self.renderSetAllSuitesTargetCount()
            ) + HtmlGeneration.grid(grid)
        )

    def renderSetAllSuitesTargetCount(self):
        menus = []
        for count in self.TARGET_COUNT_OPTIONS:
            menus.append(
                '<a class="dropdown-item" href="{link}">{contents}</a>'.format(
                    link=self.withOptions(
                        action="update_all_suites_runs",
                        targetRuns=str(count),
                    ).urlString(),
                    contents=str(count),
                )
            )

        return """
                <div class="btn-group">
                  <a role="button" class="btn btn-xs {btnstyle}" title="{title}">{elt}</a>
                  <button class="btn btn-xs {btnstyle} dropdown-toggle dropdown-toggle-split" type="button" id="dropdownMenuButton" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false">
                  </button>
                  <div class="dropdown-menu" aria-labelledby="dropdownMenuButton">
                    {dd_items}
                  </div>

                </div>
                """.format(
            elt=0,
            title="Total number of runs of all tests we want.",
            dd_items="".join(menus),
            btnstyle="btn-outline-secondary",
        )

    def renderIncreaseSuiteTargetCount(self, suite):
        menus = []
        for count in self.TARGET_COUNT_OPTIONS:
            menus.append(
                '<a class="dropdown-item" href="{link}">{contents}</a>'.format(
                    link=self.withOptions(
                        action="update_suite_runs",
                        suite=suite.testDefinitionSummary.name,
                        targetRuns=str(count),
                    ).urlString(),
                    contents=str(count),
                )
            )

        return """
                <div class="btn-group">
                  <a role="button" class="btn btn-xs {btnstyle}" title="{title}">{elt}</a>
                  <button class="btn btn-xs {btnstyle} dropdown-toggle dropdown-toggle-split" type="button" id="dropdownMenuButton" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false">
                  </button>
                  <div class="dropdown-menu" aria-labelledby="dropdownMenuButton">
                    {dd_items}
                  </div>
                  
                </div>
                """.format(
            elt=max(suite.runsDesired, 0),
            title="Total number of runs of this test we want.",
            dd_items="".join(menus),
            btnstyle="btn-outline-secondary",
        )

    def testDependencySummary(self, t):
        """Return a single cell displaying all the builds this test depends on"""
        return TestSummaryRenderer.TestSummaryRenderer(
            self.testManager.allTestsDependedOnByTest(t), ""
        ).renderSummary()

    def childContexts(self, currentChild):
        if isinstance(currentChild.primaryObject(), ComboContexts.CommitAndFilter):
            if currentChild.parentLevel == 0:
                return [
                    self.contextFor(
                        ComboContexts.CommitAndFilter(
                            commit=self.commit,
                            configurationName=g,
                            projectName=self.projectFilter,
                            parentLevel=0,
                        )
                    )
                    for g in [""]
                    + sorted(
                        set(
                            [
                                t.testDefinitionSummary.configuration
                                for t in self.testManager.allTestsForCommit(self.commit)
                                if not self.projectFilter
                                or t.testDefinitionSummary.project == self.projectFilter
                            ]
                        )
                    )
                ]
            else:
                return [
                    self.contextFor(
                        ComboContexts.CommitAndFilter(
                            commit=self.commit,
                            configurationName=self.configFilter,
                            projectName=g,
                            parentLevel=1,
                        )
                    )
                    for g in [""]
                    + sorted(
                        set(
                            [
                                t.testDefinitionSummary.project
                                for t in self.testManager.allTestsForCommit(self.commit)
                                if not self.configFilter
                                or t.testDefinitionSummary.configuration
                                == self.configFilter
                            ]
                        )
                    )
                ]

        if isinstance(currentChild.primaryObject(), self.database.Test):
            if currentChild.primaryObject().testDefinitionSummary.type == "Build":
                return [
                    self.contextFor(t)
                    for t in sorted(
                        self.allTests(), key=lambda t: t.testDefinitionSummary.name
                    )
                    if t.testDefinitionSummary.type == "Build"
                ]
            if currentChild.primaryObject().testDefinitionSummary.type == "Test":
                return [
                    self.contextFor(t)
                    for t in sorted(
                        self.allTests(), key=lambda t: t.testDefinitionSummary.name
                    )
                    if t.testDefinitionSummary.type == "Test"
                ]

        return []

    def parentContext(self):
        if self.parentLevel < 2:
            return self.contextFor(
                ComboContexts.CommitAndFilter(
                    self.commit,
                    self.configFilter,
                    self.projectFilter,
                    self.parentLevel + 1,
                )
            ).withOptions(**self.options)

        branch, name = self.testManager.bestCommitBranchAndName(self.commit)

        if branch:
            return self.contextFor(
                ComboContexts.BranchAndFilter(
                    branch, self.configFilter, self.projectFilter, 2
                )
            )

        return self.contextFor(self.commit.repo)

    def renderMenuItemText(self, isHeader):
        return (
            octicon(self.appropriateIcon()) if isHeader else ""
        ) + self.appropriateLinkName()

    def renderPostViewSelector(self):
        tests = self.allTests()
        all_tests = [x for x in tests if x.testDefinitionSummary.type == "Test"]
        all_builds = [x for x in tests if x.testDefinitionSummary.type == "Build"]

        return (
            self.renderLinkToSCM(big=True).render()
            + "&nbsp;&nbsp;&nbsp;&nbsp;Testing:&nbsp;"
            + self.dropdownForTestPrioritization()
            + "&nbsp;&nbsp;Builds:&nbsp;&nbsp;"
            + TestSummaryRenderer.TestSummaryRenderer(all_builds, "").renderSummary()
            + "&nbsp;&nbsp;Tests:&nbsp;"
            + TestSummaryRenderer.TestSummaryRenderer(all_tests, "").renderSummary()
        )

    def borrowFromContextIfPossible(self, curContext):
        if isinstance(curContext.primaryObject(), ComboContexts.CommitAndFilter):
            if curContext.parentLevel < self.parentLevel and self.parentLevel == 2:
                return self.contextFor(
                    ComboContexts.CommitAndFilter(
                        self.commit, curContext.configFilter, curContext.projectFilter
                    )
                )

        return self
