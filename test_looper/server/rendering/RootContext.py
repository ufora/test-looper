import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration

class RootContext(Context.Context):
    def __init__(self, renderer, options):
        Context.Context.__init__(self, renderer, options)

    def consumePath(self, path):
        if path and path[0] in ["repos", "deployments", "machines"]:
            return self.renderer.contextFor(path[0], self.options), path[1:]

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

        renderer = TestGridRenderer(test_rows, list(branches), None)

        grid_headers = renderer.getGridHeaders(None)

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

            row.extend(renderer.render_row(branch, None))

        return grid_headers, grid
