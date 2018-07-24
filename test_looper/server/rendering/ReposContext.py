import test_looper.server.rendering.Context as Context
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer
import test_looper.server.rendering.ComboContexts as ComboContexts
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer

card = HtmlGeneration.card

class ReposContext(Context.Context):
    def __init__(self, renderer, options):
        Context.Context.__init__(self, renderer, options)
        self.options = options

    def renderLink(self):
        return HtmlGeneration.link("Repos", self.urlString())

    def consumePath(self, path):
        if path:
            path, remainder = self.popToDash(path)

            repo = self.database.Repo.lookupAny(name = "/".join(path))

            if repo:
                return self.renderer.contextFor(repo, self.options), remainder

            return None, path

        return None, path

    def urlBase(self):
        return "repos"

    def primaryObject(self):
        return "repos"

    def renderPageBody(self):
        headers, grid = self.grid()

        if not headers:
            res = card("No repos found")
        else:
            res = HtmlGeneration.grid(headers+grid, header_rows=len(headers))
        
        return res

    def grid(self):
        repos = self.database.Repo.lookupAll(isActive=True)

        repos = [r for r in repos if self.renderer.wantsToShowRepo(r)]
            
        if not repos:
            return [], []

        repos = sorted(
            repos, 
            key=lambda repo:
                (repo.commitsWithTests == 0, repo.name)
            )

        LOOKBACK = 5

        grid_headers = [["REPO NAME", "PRIMARY TEST BRANCH"] + [""] * (LOOKBACK)]

        grid = []

        for repo in repos:
            if repo.commitsWithTests:
                branch = self.primaryBranchForRepo(repo)

                if branch:
                    testRow = [self.contextFor(branch).renderLink(includeRepo=False)]

                    testRow += self.contextFor(branch).topNCommitTestSummaryRow(LOOKBACK)
                else:
                    testRow = []

                grid.append([
                    self.contextFor(repo).renderLink()
                    ] + testRow)

        return grid_headers, grid

    def primaryBranchForRepo(self, repo):
        branches = [b for b in self.database.Branch.lookupAll(repo=repo)
            if b.branchname.endswith("master-looper")]

        if len(branches) == 1:
            return branches[0]

        for branchname in ["master", "svn-master"]:
            master = self.database.Branch.lookupAny(reponame_and_branchname=(repo.name, branchname))
            if master:
                return master

    def childContexts(self, currentChild):
        children = []

        for r in sorted(self.database.Repo.lookupAll(isActive=True),key=lambda r:r.name):
            if self.renderer.wantsToShowRepo(r):
                if r.commitsWithTests or r == currentChild:
                    children.append(r)

        return [self.contextFor(x) for x in children]

    def parentContext(self):
        return self.contextFor("root")
        
    def renderMenuItemText(self, isHeader):
        return "Repos"




