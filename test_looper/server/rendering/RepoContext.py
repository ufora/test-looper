import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer
import test_looper.server.rendering.ComboContexts as ComboContexts
import time
import cgi

octicon = HtmlGeneration.octicon
card = HtmlGeneration.card

class RepoContext(Context.Context):
    def __init__(self, renderer, repo, options):
        Context.Context.__init__(self, renderer, options)
        self.repo = repo
        self.reponame = self.repo.name
        self.displayName = renderer.repoDisplayName(self.repo.name)
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
        return HtmlGeneration.link(self.displayName, self.urlString())

    def primaryObject(self):
        return self.repo

    def urlBase(self):
        return "repos/" + self.reponame

    def contextViews(self):
        return ["branches", "configuration", "logs"]

    def renderViewMenuItem(self, view):
        if view == "branches":
            return "Branches"
        if view == "configuration":
            return "Configuration"
        if view == "logs":
            return "Logs"
        return view

    def renderViewMenuMouseoverText(self, view):
        if view == "branches":
            return "Branches contained in this commit that have tests"
        if view == "configuration":
            return "Configure auto-looper-branch-creation"
        if view == "logs":
            return "Logs for automatic branch creation"
        return view

    def renderPageBody(self):
        view = self.currentView()

        if view == "branches":
            headers, grid = self.grid()
            return HtmlGeneration.grid(headers+grid, header_rows=len(headers))
        if view == "configuration":
            return self.configurationView()
        if view == "logs":
            return self.logsView()

    def logsView(self):
        grid = [["Timestamp", "Message"]]
        log = self.repo.branchCreateLogs
        if not log:
            return card("No logs so far")

        while log and len(grid) < 100:
            grid.append([time.asctime(time.gmtime(log.timestamp)), "<pre>" + cgi.escape(log.msg) + "</pre>"])
            log = log.prior

        return HtmlGeneration.grid(grid)


    def renderTemplateUpdateForm(self, template):
        def textArea(name, val, short_desc, long_desc):
            return """
              <div class="form-group">
                <label for="{name}_{id}">{short_desc}</label>
                <textarea rows={rows} name="{name}" class="form-control" id="{name}_{id}" aria-describedby="{name}_help_{id}">{val}</textarea>
                <small id="{name}_help_{id}" class="form-text text-muted">{long_desc}</small>
              </div>
            """.format(name=name, id=template._identity, short_desc=short_desc, long_desc=long_desc,val=cgi.escape(val), rows=len(val.split("\n")) + 1)

        def simpleInput(name, val, short_desc, long_desc):
            return """
              <div class="form-group">
                <label for="{name}_{id}">{short_desc}</label>
                <input type="text" rows={rows} name="{name}" class="form-control" id="{name}_{id}" aria-describedby="{name}_help_{id}" value="{val}">
                <small id="{name}_help_{id}" class="form-text text-muted">{long_desc}</small>
              </div>
            """.format(name=name, id=template._identity, short_desc=short_desc, long_desc=long_desc,val=cgi.escape(val, quote=True), rows=len(val.split("\n")) + 1)

        def checkbox(name, long_desc, val):
            return """
              <div class="form-check">
                <input name="{name}" class="form-check-input" type="checkbox" id="{name}_{id}" value="True" {val}>
                <label for="{name}_{id}" class="form-check-label">{long_desc}</label>
              </div>
            """.format(name=name, id=template._identity, long_desc=long_desc,val="checked" if val else "")


        return """
        <form method="GET" action="{url}">
          <input type="hidden" name="view" value="configuration" /> 
          <input type="hidden" name="action" value="update_template" /> 
          <input type="hidden" name="identity" value="{id}" /> 
          <div class="form-group row">
              <div class="col">
                  {includes}
              </div>
              <div class="col">
                  {excludes}
              </div>
          </div>
          <div class="form-group row">
              <div class="col">
                  {branch}
              </div>
              <div class="col">
                  {suffix}
              </div>
          </div>
          <div class="form-group row">
              <div class="col">
                  {def_to_replace}
              </div>
              <div class="col">
                  {disableOtherAutos}
                  {autoprioritizeBranch}
                  {deleteOnUnderlyingRemoval}
              </div>
          </div>
          <div>&nbsp;</div>
          <button type="submit" class="btn btn-primary">Update</button>
        </form>
        """.format(
            url=self.withOptionsReset().urlString(),
            id=template._identity,
            includes=textArea("include_pats", "\n".join(template.globsToInclude), "Branches to include", "Glob patterns for branchnames that should trigger this"),
            excludes=textArea("exclude_pats", "\n".join(template.globsToExclude), "Branches to exclude", "Glob patterns for branchnames that should not trigger this"),
            suffix=simpleInput("suffix", template.suffix, "Suffix to append", "Suffix to append to the branchname"),
            branch=simpleInput("branch", template.branchToCopyFrom, "Branch to copy", "Name of the branch to duplicate"),
            def_to_replace=simpleInput("def_to_replace", template.def_to_replace, "Reference to update", "Name of the specific reference to update"),
            disableOtherAutos=checkbox(
                "disableOtherAutos", 
                "Only allow the primary tracking branch to float. (if not checked, just copy the settings from the underlying).",
                template.disableOtherAutos
                ),
            autoprioritizeBranch=checkbox(
                "autoprioritizeBranch", 
                "Autoprioritize the branch when it's created.", 
                template.autoprioritizeBranch
                ),
            deleteOnUnderlyingRemoval=checkbox(
                "deleteOnUnderlyingRemoval", 
                "If the underlying feature branch gets deleted, remove this branch too.", 
                template.deleteOnUnderlyingRemoval
                )
            )

    def configurationView(self):
        if self.repo.branchCreateTemplates is None:
            self.repo.branchCreateTemplates = []

        if self.options.get('action', None) == "new_template":
            self.repo.branchCreateTemplates = list(self.repo.branchCreateTemplates) + [
                self.database.BranchCreateTemplate.New()
                ]

            return HtmlGeneration.Redirect(self.withOptions(action=None).urlString())
        if self.options.get('action', None) == "update_template":
            template = self.database.BranchCreateTemplate(str(self.options.get("identity")))
            assert template.exists() and template in self.repo.branchCreateTemplates

            template.globsToInclude = [str(x) for x in self.options.get("include_pats").split("\n")]
            template.globsToExclude = [str(x) for x in self.options.get("exclude_pats").split("\n")]
            template.suffix = str(self.options.get("suffix"))
            template.branchToCopyFrom = str(self.options.get("branch"))
            template.def_to_replace = str(self.options.get("def_to_replace"))
            template.disableOtherAutos = bool(self.options.get("disableOtherAutos"))
            template.autoprioritizeBranch = bool(self.options.get("autoprioritizeBranch"))
            template.deleteOnUnderlyingRemoval = bool(self.options.get("deleteOnUnderlyingRemoval"))

            return HtmlGeneration.Redirect(self.withOptions(action=None).urlString())
        
        result = ""

        for template in self.repo.branchCreateTemplates:
            result += card(self.renderTemplateUpdateForm(template))

        result += card(
            HtmlGeneration.Link(
                self.withOptions(action='new_template').urlString(),
                "Create new Branch Template",
                is_button=True,
                button_style=self.renderer.disable_if_cant_write('btn-primary btn-xs'),
                hover_text="Create a new branch-creation template."
                ).render()
            )

        return result        

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
            lambda b: test_rows.get(b, []),
            lambda group: "",
            lambda group, row: "",
            lambda t: ""
            )

        def interlace(h):
            return [
                [{"content": x, "colspan": 2} for x in h[::2]],
                [""] + [{"content": x, "colspan": 2} for x in h[1::2]]
                ]

        grid_headers = [gridRenderer.headers()]
        if sum([len(x) for x in grid_headers[0]]) > 100:
            grid_headers = interlace(grid_headers[0])

        if grid_headers:
            for additionalHeader in reversed(["TEST", "BRANCH NAME", "TOP TESTED COMMIT"]):
                grid_headers = [[""] + g for g in grid_headers]
                grid_headers[-1][0] = additionalHeader
        else:
            grid_headers = [["TEST", "BRANCH NAME", "TOP TESTED COMMIT"]]

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

            if best_commit[branch]:
                row.append(self.contextFor(best_commit[branch]).renderLink(includeRepo=False, includeBranch=False))
            else:
                row.append("")

            row.extend(gridRenderer.gridRow(branch))

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
        return (octicon("repo") if isHeader else "") + self.displayName
