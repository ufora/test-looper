import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.rendering.ComboContexts as ComboContexts
import cgi
import urllib
octicon = HtmlGeneration.octicon
card = HtmlGeneration.card

class BranchContext(Context.Context):
    def __init__(self, renderer, branch, configurationFilter, projectFilter, parentLevel, options):
        Context.Context.__init__(self, renderer, options)
        self.branch = branch
        self.parentLevel = parentLevel
        self.repo = branch.repo
        self.reponame = branch.repo.name
        self.branchname = branch.branchname
        self.options = options
        self.configurationFilter = configurationFilter
        self.projectFilter = projectFilter

    def appropriateIcon(self, isInMenu):
        if self.parentLevel == 0 and isInMenu:
            return "database"
        if self.parentLevel == 1 and isInMenu:
            return "circuit-board"

        return "git-branch"

    def appropriateLinkName(self, isInMenu):
        if self.parentLevel == 0 and isInMenu:
            return self.configurationFilter or '<span class="text-muted">all configs</span>'

        if self.parentLevel == 1 and isInMenu:
            return self.projectFilter or '<span class="text-muted">all projects</span>'

        return self.branchname

    def renderNavbarLink(self, isInMenu=False):
        return octicon(self.appropriateIcon(isInMenu)) + self.renderLink(includeRepo=False, isInMenu=isInMenu)

    def renderLink(self, includeRepo=True, isInMenu=False):
        return HtmlGeneration.link(self.appropriateLinkName(isInMenu), self.urlString())

    def primaryObject(self):
        return ComboContexts.BranchAndFilter(self.branch, self.configurationFilter, self.projectFilter, self.parentLevel)

    def parentContext(self):
        if self.parentLevel < 2:
            return self.contextFor(
                ComboContexts.BranchAndFilter(self.branch, self.configurationFilter, self.projectFilter, self.parentLevel+1)
                ).withOptions(**self.options)

        return self.contextFor(self.branch.repo)

    def urlBase(self):
        res = "repos/" + self.reponame + "/-/branches/" + self.branchname
        if self.configurationFilter:
            res += "/-/configurations/" + self.configurationFilter

        if self.projectFilter:
            res += "/-/projects/" + self.projectFilter

        return res

    def maxCommitCount(self):
        return int(self.options.get("max_commit_count", 100))

    def renderPageBody(self):
        view = self.options.get("view", "commits")

        if view == "commits":
            return self.testDisplayForCommits(
                self.testManager.commitsToDisplayForBranch(self.branch, self.maxCommitCount())
                )
        elif view == "pins":
            pinGrid = self.pinGridWithUpdateButtons(self.branch)

            if len(pinGrid) > 1:
                pinContents = (
                    HtmlGeneration.grid(pinGrid)
                    )
            else:
                pinContents = card("Branch has no pins.")

            return pinContents

    def contextViews(self):
        return ["commits", "pins"]

    def renderViewMenuItem(self, view):
        if view == "commits":
            return "Commits"
        if view == "pins":
            return octicon("pin") + "Branch Pins"
        return view

    def pruneCommitList(self, commits, parents, children):
        if self.options.get("show_all_commits", False):
            return

        if not (self.configurationFilter or self.projectFilter):
            return

        def shouldIncludeTest(test):
            if self.projectFilter and test.testDefinitionSummary.project != self.projectFilter:
                return False
            if self.configurationFilter and test.testDefinitionSummary.configuration != self.configurationFilter:
                return False
            return True

        def wantsCommit(c):
            if not c.data:
                return False

            if not c.data.testSetsTopLevel:
                return True

            if 'all' in c.data.triggeredTestSets or 'all' in c.userEnabledTestSets:
                return True

            for testSet, tests in c.data.testSetsTopLevel.items():
                if testSet in c.data.triggeredTestSets or testSet in c.userEnabledTestSets:
                    for t in tests:
                        if shouldIncludeTest(c.data.tests[t]):
                            return True

            return False

        discardable = set()

        for c in commits:
            if not wantsCommit(c) and len(parents.get(c.hash,[])) <= 1 and len(children.get(c.hash,[])) <= 1:
                discardable.add(c.hash)

        discardRoots = {}
        for discardableHash in discardable:
            c = children.get(discardableHash,[])
            p = parents.get(discardableHash,[])

            if (not c or c[0] not in discardable):
                depth = 0

                discardTailHash = discardableHash

                while discardTailHash and discardTailHash in discardable:
                    depth += 1
                    if parents[discardTailHash]:
                        discardTailHash = parents[discardTailHash][0]
                    else:
                        discardTailHash = None

                if c:
                    discardRoots[c[0]] = depth

                if discardTailHash:
                    children[discardTailHash] = [c[0]] if c else []

                if c:
                    parents[c[0]] = [discardTailHash] if discardTailHash else []

        commits[:] = [c for c in commits if c.hash not in discardable]

        return discardRoots

    def testDisplayForCommits(self, commits):
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

        discardRoots = self.pruneCommitList(commits, parents, children) or {}

        for c in commits:
            if not parents[c.hash]:
                branchname = "branch_%s" % len(branches)

                commit_string += 'var %s = gitgraph.branch("%s");\n' % (branchname, branchname)
                branches[c.hash] = branchname

        #we need to walk the commits from bottom to top. E.g. the ones with no parents go first.
        order = {}
        unordered_parents = {h: set(parents[h]) for h in parents}
        edges = [h for h in unordered_parents if not unordered_parents[h]]

        needingOrder = set([c.hash for c in commits])
        while edges and needingOrder:
            e = edges.pop()

            order[e] = max([order[p]+1 for p in parents[e]] + [0])
            needingOrder.discard(e)

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
                    c.data.subject.replace("\\","\\\\").replace("'", "\\'"),
                    c.hash
                    )

            elif len(parentsWeHave) == 1:
                #push a commit onto the branch
                our_branch = branches[(parentsWeHave[0], c.hash)]

                if c.hash in discardRoots:
                    commit_string += "%s.commit({sha1: '%s', message: '%s', detailId: 'commit_%s'});\n" % (
                        our_branch,
                        "skipping",
                        "%s commits" % discardRoots[c.hash],
                        c.hash + "_"
                        )

                commit_string += "%s.commit({sha1: '%s', message: '%s', detailId: 'commit_%s'});\n" % (
                    our_branch,
                    c.hash,
                    c.data.subject.replace("\\","\\\\").replace("'", "\\'"),
                    c.hash
                    )
            else:
                our_branch = branches[(parentsWeHave[0], c.hash)]
                other_branch = branches[(parentsWeHave[1], c.hash)]

                commit_string += "%s.merge(%s, {sha1: '%s', message: '%s', detailId: 'commit_%s'}).delete();" % (other_branch, our_branch,
                    c.hash,
                    c.data.subject.replace("\\","\\\\").replace("'", "\\'"),
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

        gridRenderer = self.getGridRenderer(commits)

        grid = [["COMMIT"] + gridRenderer.headers() + [""]]

        for c in reversed(commits):
            gridrow = self.getBranchCommitRow(c, gridRenderer)

            grid.append(gridrow)

            if c.hash in discardRoots:
                grid.append([{'content': '<span class="text-muted">%s commits filtered out</span>' % discardRoots[c.hash], 'colspan': len(grid[0])}])

            # if c.hash in discardRoots:
            #     div = '''
            #         <div style="position: relative; bottom: 6px; left: 17px; width: 100px; height: 25px">
            #         <span class="text-muted" style="font-size: 12px">(%s commits)</span>
            #         </div>''' % discardRoots[c.hash]

            #     gridrow[0] = '<div style="position:relative; width:50px; height:25px">%s%s</div>' % (gridrow[0].render(), div)



        grid = HtmlGeneration.grid(grid, rowHeightOverride=36)

        canvas = HtmlGeneration.gitgraph_canvas_setup(commit_string, grid)

        return detail_divs + canvas

    def getGridRenderer(self, commits):
        projectFilter = self.projectFilter
        configFilter = self.configurationFilter

        def shouldIncludeTest(test):
            if self.projectFilter and test.testDefinitionSummary.project != self.projectFilter:
                return False
            if self.configurationFilter and test.testDefinitionSummary.configuration != self.configurationFilter:
                return False
            return True

        projects = set()
        configurations = set()

        for c in commits:
            for t in self.testManager.allTestsForCommit(c):
                if shouldIncludeTest(t):
                    projects.add(t.testDefinitionSummary.project)
                    configurations.add(t.testDefinitionSummary.configuration)

        if not projectFilter and len(projects) == 1:
            projectFilter = list(projects)[0]

        if not configFilter and len(configurations) == 1:
            configFilter = list(configurations)[0]

        if self.options.get("expanded_columns") or len(projects) == 1 and len(configurations) == 1:
            #we need the branch in the cache-key because the branch is included in the url
            cacheKey = (self.branch, True, projectFilter, configFilter)
            if not projectFilter:
                return TestGridRenderer.TestGridRenderer(commits,
                    lambda c: [t for t in self.testManager.allTestsForCommit(c)
                            if shouldIncludeTest(t)]
                        if c.data else [],
                    lambda group: self.contextFor(ComboContexts.BranchAndFilter(self.branch, configFilter, group, parentLevel=1)).renderNavbarLink(isInMenu=True),
                    lambda group, row: self.contextFor(ComboContexts.CommitAndFilter(row, configFilter, group)).urlString(),
                    lambda test: test.testDefinitionSummary.project,
                    cacheName=cacheKey,
                    database=self.testManager.database
                    )

            if not configFilter:
                return TestGridRenderer.TestGridRenderer(commits,
                    lambda c: [t for t in self.testManager.allTestsForCommit(c)
                            if shouldIncludeTest(t)]
                        if c.data else [],
                    lambda group: self.contextFor(ComboContexts.BranchAndFilter(self.branch, group, projectFilter, parentLevel=0)).renderNavbarLink(isInMenu=True),
                    lambda group, row: self.contextFor(ComboContexts.CommitAndFilter(row, group, projectFilter)).urlString(),
                    lambda test: test.testDefinitionSummary.configuration,
                    cacheName=cacheKey,
                    database=self.testManager.database
                    )

            return TestGridRenderer.TestGridRenderer(commits,
                lambda c: [t for t in self.testManager.allTestsForCommit(c)
                        if shouldIncludeTest(t)]
                    if c.data else [],
                lambda group: "",
                lambda group, row: self.contextFor(ComboContexts.CommitAndFilter(row, configFilter, projectFilter)).urlString(),
                lambda test: test.testDefinitionSummary.project + " / " + test.testDefinitionSummary.configuration,
                cacheName=cacheKey,
                database=self.testManager.database
                )
        else:
            cacheKey = (self.branch, False, projectFilter, configFilter)

            return TestGridRenderer.TestGridRenderer(commits,
                lambda c: [t for t in self.testManager.allTestsForCommit(c)
                        if shouldIncludeTest(t)]
                    if c.data else [],
                lambda group: self.withOptions(expanded_columns='true').renderLink().withTextReplaced("%s projects over %s configurations" % (len(projects), len(configurations))),
                lambda group, row: self.contextFor(ComboContexts.CommitAndFilter(row, configFilter, projectFilter)).urlString(),
                lambda test: "",
                cacheName=cacheKey,
                database=self.testManager.database
                )


    def getContextForCommit(self, commit):
        if self.configurationFilter or self.projectFilter:
            return self.contextFor(
                ComboContexts.CommitAndFilter(
                    commit,
                    self.configurationFilter,
                    self.projectFilter
                    )
                )
        return self.contextFor(commit)

    def getBranchCommitRow(self, commit, renderer):
        row = [self.getContextForCommit(commit).renderLinkWithShaHash()]

        all_tests = self.testManager.allTestsForCommit(commit)

        if all_tests:
            row[-1] += "&nbsp;" + self.contextFor(commit).dropdownForTestPrioritization()

        row.extend(renderer.gridRow(commit))

        if False:
            row.append(
                HtmlGeneration.lightGrey("waiting to load commit")
                        if not commit.data
                else HtmlGeneration.lightGrey("no test file")
                        if commit.data.noTestsFound
                else HtmlGeneration.lightGrey("invalid test file")
                        if commit.data.testDefinitionsError
                else ""
                )

        row.append(self.contextFor(commit).renderSubjectAndAuthor())
        row.append(self.renderer.deleteAllTestRunsButton(commit._identity))

        return row

    def collapseName(self, name, env):
        name = "/".join([p.split(":")[0] for p in name.split("/")])
        env = env.split("/")[-1]
        if name.endswith("/" + env):
            name = name[:-1-len(env)]
        return name

    def pinGridWithUpdateButtons(self, branch):
        lines = [["status", "refname", "Cur Commit", "Target Branch"]]

        for refname, repoRef in sorted(branch.head.data.repos.items()):
            if repoRef.matches.Pin:
                lines.append(
                    [self.renderPinUpdateLink(branch, refname, repoRef),
                    refname,
                    self.renderPinReference(refname, repoRef),
                    repoRef.branchname() if repoRef.branchname() else ""
                    ])

        return lines

    def topNCommitTestSummaryRow(self, N):
        testRow = []

        for commit in self.renderer.testManager.topNPrioritizedCommitsForBranch(self.branch, N):
            testRow.append(
                TestSummaryRenderer.TestSummaryRenderer(
                    self.renderer.allTestsForCommit(commit),
                    testSummaryUrl=self.contextFor(commit).urlString()
                    ).renderSummary(
                    label='<span style="display: inline-block; width:60px">%s</span>' % (
                        self.contextFor(commit).renderLink(False,False).render()
                        ),
                    extraStyle="border"
                    )
                )

        return testRow


    def renderPinUpdateLink(self, branch, reference_name, repoRef):
        if repoRef.auto and repoRef.auto != "false":
            return HtmlGeneration.lightGrey("marked auto")
        else:
            commit = branch.head

            targetRepoName = repoRef.reponame()

            target_branch = self.testManager.database.Branch.lookupAny(reponame_and_branchname=(targetRepoName,repoRef.branch))

            if not target_branch:
                return HtmlGeneration.lightGrey("unknown branch %s" % repoRef.branch)

            if target_branch.head.hash == repoRef.commitHash():
                return HtmlGeneration.lightGrey("up to date")

            message = "push commit updating pin of %s from %s to %s" % (reference_name, target_branch.head.hash, repoRef.commitHash())

            params = {
                "redirect": self.redirect(),
                "repoName": commit.repo.name,
                "branchName": branch.branchname,
                "ref": reference_name
                }

            return ('<a href="/updateBranchPin?' + urllib.parse.urlencode(params) + '" title="' + message + '">'
                '<span class="octicon octicon-sync " aria-hidden="true" />'
                '</a>')

    def renderPinReference(self, reference_name, repoRef, includeName=False):
        if includeName:
            preamble = reference_name + "-&gt;"
        else:
            preamble = ""

        repoName = repoRef.reponame()
        commitHash = repoRef.commitHash()

        repo = self.testManager.database.Repo.lookupAny(name=repoName)
        if not repo:
            return preamble + HtmlGeneration.lightGreyWithHover(repoRef.reference, "Can't find repo %s" % repoName)

        commit = self.testManager.database.Commit.lookupAny(repo_and_hash=(repo, commitHash))
        if not commit:
            return preamble + HtmlGeneration.lightGreyWithHover(repoRef.reference[:--30], "Can't find commit %s" % commitHash[:10])

        branches = {k.branchname: v for k,v in self.testManager.commitFindAllBranches(commit).items()}

        if repoRef.branch not in branches:
            return preamble + self.contextFor(commit).renderLink()

        return preamble + self.contextFor(commit).renderLink()

    def consumePath(self, path):
        if path and path[0] == "configurations":
            groupPath, remainder = self.popToDash(path[1:])

            if not path:
                return None, path

            configurationName = "/".join(groupPath)

            return self.contextFor(ComboContexts.BranchAndFilter(self.branch, configurationName, self.projectFilter)), remainder

        if path and path[0] == "projects":
            groupPath, remainder = self.popToDash(path[1:])

            if not path:
                return None, path

            projectName = "/".join(groupPath)

            return self.contextFor(ComboContexts.BranchAndFilter(self.branch, self.configurationFilter, projectName)), remainder

        return None, path

    def childContexts(self, currentChild):
        if isinstance(currentChild.primaryObject(), (self.database.Commit, ComboContexts.CommitAndFilter)):
            commit = currentChild.primaryObject()
            if isinstance(commit, ComboContexts.CommitAndFilter):
                commit = commit.commit

            children = []

            commitsInBetween = set()
            commitsToCheck = set()
            commitsToCheck.add(self.branch.head)

            while commitsToCheck:
                c = commitsToCheck.pop()

                if c and c not in commitsInBetween:
                    commitsInBetween.add(c)
                    if c.data:
                        for p in c.data.parents:
                            commitsToCheck.add(p)

            #show 10 commits above and below
            return [self.contextFor(ComboContexts.CommitAndFilter(x, self.configurationFilter, self.projectFilter, 2)) for x in
                list(reversed(self.testManager.getNCommits(commit, 10, "above", commitsInBetween))) + [commit] +
                    self.testManager.getNCommits(commit, 10, "below")
                ]

        if isinstance(currentChild.primaryObject(), ComboContexts.BranchAndFilter):
            self.database.addCalculationCache(BranchContext.computeAllConfigs)
            self.database.addCalculationCache(BranchContext.computeAllProjects)

            if currentChild.parentLevel == 0:
                return [self.contextFor(
                    ComboContexts.BranchAndFilter(branch=self.branch, configurationName=g, projectName=self.projectFilter, parentLevel=0)
                    )
                        for g in [""] + self.database.lookupCachedCalculation(
                            BranchContext.computeAllConfigs,
                            (self.testManager, self.branch, self.maxCommitCount(), self.projectFilter)
                            )
                    ]
            else:
                return [self.contextFor(
                    ComboContexts.BranchAndFilter(branch=self.branch, configurationName=self.configurationFilter, projectName=g, parentLevel=1)
                    )
                        for g in [""] + self.database.lookupCachedCalculation(
                            BranchContext.computeAllProjects,
                            (self.testManager, self.branch, self.maxCommitCount(), self.configurationFilter)
                            )
                    ]

        else:
            return []

    @staticmethod
    def computeAllConfigs(testManager, branch, maxCommitCount, projectFilter):
        return sorted(
            set([t.testDefinitionSummary.configuration
                    for commit in testManager.commitsToDisplayForBranch(branch, maxCommitCount)
                    for t in testManager.allTestsForCommit(commit)
                        if t.testDefinitionSummary.project == projectFilter or not projectFilter
                ]))

    @staticmethod
    def computeAllProjects(testManager, branch, maxCommitCount, configFilter):
        return sorted(
            set([t.testDefinitionSummary.project
                    for commit in testManager.commitsToDisplayForBranch(branch, maxCommitCount)
                    for t in testManager.allTestsForCommit(commit)
                        if t.testDefinitionSummary.configuration == configFilter or not configFilter
                ]))

    def renderPostViewSelector(self):
        if self.options.get("view", "commits") != "commits":
            return ""

        res = []
        is_show_all_commits = self.options.get("show_all_commits", False)

        if (self.configurationFilter or self.projectFilter):
            res.append(
                HtmlGeneration.Link(
                    self.withOptions(show_all_commits='true' if not is_show_all_commits else None).urlString(),
                    "Show All Commits",
                    is_button=True,
                    button_style='btn-%s btn-xs' % ("outline-primary" if not is_show_all_commits else "primary"),
                    hover_text="Show all commits, not just those directly affecting this project."
                    ).render()
                )

        is_detail = bool(self.options.get('expanded_columns',False))

        res.append(
            HtmlGeneration.Link(
                self.withOptions(expanded_columns='true' if not is_detail else None).urlString(),
                "Detail View",
                is_button=True,
                button_style='btn-%s btn-xs' % ("outline-primary" if not is_detail else "primary"),
                hover_text="Break out projects and configurations into columns if selected."
                ).render()
            )

        res.append(self.renderer.toggleBranchUnderTestLink(self.branch).render())
        return "&nbsp;&nbsp;".join(res)


    def borrowFromContextIfPossible(self, curContext):
        if isinstance(curContext.primaryObject(), ComboContexts.BranchAndFilter):
            if curContext.parentLevel < self.parentLevel and self.parentLevel == 2:
                return self.contextFor(
                    ComboContexts.BranchAndFilter(
                        self.branch,
                        curContext.configurationFilter,
                        curContext.projectFilter
                        )
                    )

        return self

    def renderMenuItemText(self, isHeader):
        return (octicon(self.appropriateIcon(True)) if isHeader else "") + self.appropriateLinkName(True)
