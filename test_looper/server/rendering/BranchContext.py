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
    def __init__(self, renderer, branch, configurationFilter, projectFilter, options):
        Context.Context.__init__(self, renderer, options)
        self.branch = branch
        self.repo = branch.repo
        self.reponame = branch.repo.name
        self.branchname = branch.branchname
        self.options = options
        self.configurationFilter = configurationFilter
        self.projectFilter = projectFilter

    def appropriateIcon(self):
        if self.configurationFilter:
            icon = "database"
        elif self.projectFilter:
            icon = "circuit-board"
        else:
            icon = "git-branch"
        return icon

    def appropriateLinkName(self):
        if self.configurationFilter:
            text = self.configurationFilter
        elif self.projectFilter:
            text = self.projectFilter
        else:
            text = self.branchname
        
        return text

    def renderNavbarLink(self):
        return octicon(self.appropriateIcon()) + self.renderLink(includeRepo=False)

    def renderLink(self, includeRepo=True):
        return HtmlGeneration.link(self.appropriateLinkName(), self.urlString())

    def primaryObject(self):
        if not self.configurationFilter and not self.projectFilter:
            return self.branch
        else:
            return ComboContexts.BranchAndFilter(self.branch, self.configurationFilter, self.projectFilter)

    def parentContext(self):
        if self.configurationFilter and self.projectFilter:
            return self.contextFor(
                ComboContexts.BranchAndFilter(self.branch, None, self.projectFilter)
                ).withOptions(**self.options)

        if self.configurationFilter or self.projectFilter:
            return self.contextFor(self.branch)
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
                    c.data.subject.replace("\\","\\\\").replace("'", "\\'"),
                    c.hash
                    )

            elif len(parentsWeHave) == 1:
                #push a commit onto the branch
                our_branch = branches[(parentsWeHave[0], c.hash)]

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

        grid = HtmlGeneration.grid(grid, rowHeightOverride=36)
        
        canvas = HtmlGeneration.gitgraph_canvas_setup(commit_string, grid)

        return detail_divs + canvas

    def getGridRenderer(self, commits):
        projectFilter = self.projectFilter
        configFilter = self.configurationFilter

        def shouldIncludeTest(test):
            if test.testDefinitionSummary.disabled:
                return False
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

        if not projectFilter:
            return TestGridRenderer.TestGridRenderer(commits, 
                lambda c: [t for t in self.testManager.allTestsForCommit(c) 
                        if shouldIncludeTest(t)] 
                    if c.data else [],
                lambda group: self.contextFor(ComboContexts.BranchAndFilter(self.branch, configFilter, group)).renderNavbarLink(),
                lambda group, row: self.contextFor(ComboContexts.CommitAndFilter(row, configFilter, group)).urlString(),
                lambda test: test.testDefinitionSummary.project
                )

        if not configFilter:
            return TestGridRenderer.TestGridRenderer(commits, 
                lambda c: [t for t in self.testManager.allTestsForCommit(c) 
                        if shouldIncludeTest(t)] 
                    if c.data else [],
                lambda group: self.contextFor(ComboContexts.BranchAndFilter(self.branch, group, projectFilter)).renderNavbarLink(),
                lambda group, row: self.contextFor(ComboContexts.CommitAndFilter(row, group, projectFilter)).urlString(),
                lambda test: test.testDefinitionSummary.configuration
                )

        return TestGridRenderer.TestGridRenderer(commits, 
            lambda c: [t for t in self.testManager.allTestsForCommit(c) 
                    if shouldIncludeTest(t)] 
                if c.data else [],
            lambda group: "",
            lambda group, row: "",
            lambda test: ""
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
            row[-1] += "&nbsp;" + self.contextFor(commit).toggleCommitUnderTestLink()
        
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

        return row

    def collapseName(self, name, env):
        name = "/".join([p.split(":")[0] for p in name.split("/")])
        env = env.split("/")[-1]
        if name.endswith("/" + env):
            name = name[:-1-len(env)]
        return name

    def pinGridWithUpdateButtons(self, branch):
        lines = [["status", "refname", "Cur Commit", "Target Branch"]]

        for refname, repoRef in sorted(branch.head.data.repos.iteritems()):
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
        if isinstance(currentChild.primaryObject(), self.database.Commit):
            commit = currentChild.primaryObject()

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
            return [self.contextFor(x) for x in 
                list(reversed(self.testManager.getNCommits(commit, 10, "above", commitsInBetween))) + [commit] + 
                    self.testManager.getNCommits(commit, 10, "below")
                ]

        if isinstance(currentChild.primaryObject(), ComboContexts.BranchAndFilter):
            if currentChild.configurationFilter:
                return [self.contextFor(
                    ComboContexts.BranchAndFilter(branch=self.branch, configurationName=g, projectName=self.projectFilter)
                    )
                        for g in sorted(set([t.testDefinitionSummary.configuration
                                for commit in self.testManager.commitsToDisplayForBranch(self.branch, self.maxCommitCount())
                                for t in self.testManager.allTestsForCommit(commit) 
                                    if t.testDefinitionSummary.project == self.projectFilter
                            ]))
                    ]
            else:
                return [self.contextFor(
                    ComboContexts.BranchAndFilter(branch=self.branch, configurationName="", projectName=g)
                    )
                        for g in sorted(set([t.testDefinitionSummary.project
                                for commit in self.testManager.commitsToDisplayForBranch(self.branch, self.maxCommitCount())
                                for t in self.testManager.allTestsForCommit(commit)
                            ]))
                    ]
            
        else:
            return []

    def renderMenuItemText(self, isHeader):
        return (octicon(self.appropriateIcon()) if isHeader else "") + self.appropriateLinkName()
