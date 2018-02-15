import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer
import test_looper.server.rendering.ComboContexts as ComboContexts

octicon = HtmlGeneration.octicon

class RepoContext(Context.Context):
    def __init__(self, renderer, repo, options):
        Context.Context.__init__(self, renderer, options)
        self.repo = repo
        self.reponame = self.repo.name
        self.options = options

    def consumePath(self, path):
        if path:
            if path[0] == "commits" and len(path) > 1:
                commit = self.database.Commit.lookupAny(repo_and_hash=(self.repo, path[1]))
                if not commit:
                    return None, path
                return self.renderer.contextFor(commit, self.options), path[2:]

            if path[0] == "branches" and len(path) > 1:
                branchPath, remainder = self.popToDash(path[1:])

                branch = self.database.Branch.lookupAny(reponame_and_branchname=(self.repo.name, "/".join(branchPath)))

                if not branch:
                    return None, path

                return self.renderer.contextFor(branch, self.options), remainder
            
            return None, path

        return None, path

    def renderLink(self):
        return HtmlGeneration.link(self.reponame, self.urlString())

    def primaryObject(self):
        return self.repo

    def urlBase(self):
        return "repos/" + self.reponame

    def renderPageBody(self):
        headers, grid = self.grid()

        return HtmlGeneration.grid(headers+grid, header_rows=len(headers))

    def branchHasTests(self, branch):
        return self.renderer.branchHasTests(branch)

    def grid(self):
        branches = self.testManager.database.Branch.lookupAll(repo=self.repo)
        
        branches = sorted(branches, key=lambda b: (not self.branchHasTests(b), b.branchname))

        test_rows = {}
        best_commit = {}
        best_commit_name = {}

        for b in branches:
            best_commit[b],best_commit_name[b] = self.renderer.bestCommitForBranch(b)

            test_rows[b] = self.renderer.allTestsForCommit(best_commit[b]) if best_commit[b] else []

        gridRenderer = TestGridRenderer.TestGridRenderer(
            branches, 
            lambda b: test_rows.get(b, [])
            )

        grid_headers = [gridRenderer.headers()]

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

            row.append(self.renderer.toggleBranchUnderTestLink(branch))
            row.append(self.contextFor(branch).renderLink(includeRepo=False))

            if branch.head and branch.head.data:
                row.append(self.contextFor(branch.head).renderLinkWithSubject())
            else:
                row.append(HtmlGeneration.lightGrey("loading"))

            if best_commit[branch]:
                row.append(self.contextFor(best_commit[branch]).renderLink(includeRepo=False))
            else:
                row.append("")

            row.extend(
                gridRenderer.gridRow(
                    branch, 
                    lambda group, row: 
                        self.contextFor(ComboContexts.CommitAndConfiguration(best_commit[row], group)).urlString() 
                            if best_commit[row] else ""
                    )
                )

        return grid_headers, grid

    def childContexts(self, currentChild):
        children = []

        for b in sorted(self.testManager.database.Branch.lookupAll(repo=self.repo),key=lambda b:b.branchname):
            if self.renderer.branchHasTests(b) or b == currentChild:
                children.append(b)

        return [self.contextFor(x) for x in children]

    def parentContext(self):
        return self.contextFor("repos")

    def renderMenuItemText(self, isHeader):
        return (octicon("repo") if isHeader else "") + self.repo.name
