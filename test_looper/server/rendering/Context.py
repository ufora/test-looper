import urllib

import test_looper.server.HtmlGeneration as HtmlGeneration

octicon = HtmlGeneration.octicon

class Context(object):
    def __init__(self, renderer, options):
        self.renderer = renderer
        self.testManager = renderer.testManager
        self.database = renderer.testManager.database
        self.options = options

    @staticmethod
    def popToDash(items):
        try:
            ix = items.index("-")
            return items[:ix], items[ix+1:]
        except ValueError:
            return items, []

    def redirect(self):
        return self.renderer.redirect()        

    def urlBase(self):
        assert False, "Subclasses Implement"

    def primaryObject(self):
        assert False, "Subclasses Implement"

    def urlString(self, **kwargs):
        finalArgs = dict(self.options)
        finalArgs.update(kwargs)
        for k in kwargs:
            if kwargs[k] is None:
                del finalArgs[k]

        return "/" + self.urlBase() + ("?" + urllib.urlencode(finalArgs) if finalArgs else "")

    def splitEnvNameIntoCommitAndEnv(self, environment_name):
        tokens = environment_name.split("/")
        for i in xrange(len(tokens)):
            if Git.isShaHash(tokens[i]):
                repoName = "/".join(tokens[:i])
                hash = tokens[i]
                envName = "/".join(tokens[i+1:])

                repo = self.testManager.database.Repo.lookupAny(name=repoName)
                if repo:
                    commit = self.testManager.database.Commit.lookupAny(repo_and_hash=(repo, hash))
                    if commit:
                        return commit, envName

        return None, environment_name

    def renderPageHeader(self):
        currentObject = self.primaryObject()

        headers = []

        nav_links = [
            ('Machines', '/machines', currentObject == "machines", []),
            ('Deployments', '/deployments', currentObject == "deployments", []),
            ('Repos', '/repos', currentObject == "repos",[]),
            ('<span class="px-4"/>', False, False,[]),
            ]

        arrow_link_ix = len(nav_links)-1

        def addRepo(repo, isActive):
            dds = []

            dds.append((self.contextFor(repo).urlString(),repo.name))
            dds.append('<div class="dropdown-divider"></div>')

            for r in sorted(self.testManager.database.Repo.lookupAll(isActive=True),key=lambda r:r.name):
                if r.commitsWithTests and r != repo:
                    dds.append((self.contextFor(r).urlString(), r.name))

            nav_links.append(
                    (octicon('repo') + repo.name, "", isActive, dds)
                    )

        def addSpacer():
            nav_links.append(("/", "",False,[]))

        def addBranch(branch, isActive):
            addRepo(branch.repo, False)
            addSpacer()

            dds = []

            dds.append((self.contextFor(branch).urlString(),branch.branchname))
            dds.append('<div class="dropdown-divider"></div>')

            for b in sorted(self.testManager.database.Branch.lookupAll(repo=branch.repo),key=lambda b:b.branchname):
                if self.renderer.branchHasTests(b) and b != branch:
                    dds.append((self.contextFor(b).urlString(),b.branchname))

            if len(dds) == 2:
                dds = []

            if not dds:
                link = self.contextFor(branch).urlString()
            else:
                link = ""

            nav_links.append(
                    ('<span class="octicon octicon-git-branch" aria-hidden="true"/>' + branch.branchname, link, isActive, dds)
                    )

        def addCommit(commit, isActive):
            branch, name = self.testManager.bestCommitBranchAndName(commit)
            if branch:
                addBranch(branch, False)
            else:
                addRepo(commit.repo, False)

            addSpacer()

            nav_links.append(
                    ('Commit&nbsp;<span class="octicon octicon-git-commit" aria-hidden="true"/>' + "HEAD"+name, 
                        self.contextFor(commit).urlString(), 
                        isActive, [])
                    )

        def addTest(test, isActive):
            commit = test.commitData.commit

            addCommit(commit, False)
            branch, name = self.testManager.bestCommitBranchAndName(commit)
            addSpacer()

            if test.testDefinition.matches.Build:
                icon = 'tools'
            else:
                icon = "beaker"

            nav_links.append(
                    (octicon(icon) + '<span class="px-1"/>' + test.testDefinition.name, 
                        self.contextFor(test).urlString()
                        , isActive, [])
                    )

        def addTestRun(testRun, isActive):
            addTest(testRun.test, False)
            addSpacer()
            nav_links.append(
                (octicon("file-directory") + '<span class="px-1"/>' + testRun._identity[:8], "", isActive, [])
                )

        def addEnvironment(env, isActive):
            commit, envName = self.splitEnvNameIntoCommitAndEnv(env.environment_name)

            if commit:
                addCommit(commit, False)
                addSpacer()

            nav_links.append(
                (octicon("server") + '<span class="px-1"/>Environment ' + envName, "", isActive, [])
                )
            
        if currentObject:
            if isinstance(currentObject, self.testManager.database.Repo):
                addRepo(currentObject, True)

            if isinstance(currentObject, self.testManager.database.Branch):
                addBranch(currentObject, False)

            if isinstance(currentObject, self.testManager.database.Commit):
                commit = currentObject
                addCommit(commit, True)

            if isinstance(currentObject, self.testManager.database.Test):
                addTest(currentObject, True)

            if isinstance(currentObject, self.testManager.database.TestRun):
                addTestRun(currentObject, True)

            if hasattr(currentObject, "environment_name"):
                addEnvironment(currentObject, True)
                
        
        headers += ["""
            <nav class="navbar navbar-expand navbar-light bg-light">
            <button class="navbar-toggler" type="button" data-toggle="collapse" data-target="#navbarText" aria-controls="navbarText" aria-expanded="false" aria-label="Toggle navigation">
              <span class="navbar-toggler-icon"></span>
            </button>
            <ul class="navbar-nav mr-auto">
            """]

        for label, link, active, dropdowns in nav_links:
            elt = label
            if link:
                elt = '<a class="nav-link" href="{link}">{elt}</a>'.format(link=link,elt=elt)
            else:
                if not dropdowns:
                    elt = '<div class="navbar-text">{elt}</div>'.format(elt=elt)

            if dropdowns:
                dd_items = []
                for item in dropdowns:
                    if isinstance(item,str):
                        dd_items += [item]
                    else:
                        href, contents = item
                        dd_items += [
                            '<a class="dropdown-item" href="{link}">{contents}</a>'.format(link=href,contents=contents)
                            ]

                elt = """
                    <div class="btn-group">
                      <button class="btn {btnstyle} dropdown-toggle" type="button" id="dropdownMenuButton" data-toggle="dropdown" aria-haspopup="true" aria-expanded="false">
                        {elt}
                      </button>
                      <div class="dropdown-menu" aria-labelledby="dropdownMenuButton">
                        {dd_items}
                      </div>
                      
                    </div>
                    """.format(
                        elt=elt, 
                        dd_items = "".join(dd_items),
                        btnstyle="btn-outline-secondary" if not active else "btn-primary"
                        )

            elt = ('<li class="nav-item {is_active} px-md-1">{elt}</li>'.format(elt=elt, is_active="active" if active else ""))

            headers += [elt]

        headers += [
            '</ul>'
            ]

        if currentObject == "repos":
            headers += [
                """<span class="navbar-text pr-5"><button class="btn btn-outline-secondary btn-light">
                    <a href="{url}"><span class="octicon octicon-sync" aria-hidden="true"/> Refresh {kind}</a>
                    </button></span>
                """.format(
                    url="/refresh?" + urllib.urlencode({"redirect": self.redirect()}),
                    kind="Repos"
                    )
                ]

        headers += [
            '<span class="navbar-text">',
                self.renderer.logout_link() if self.renderer.is_authenticated() else self.renderer.login_link(),
            '</span>',
            '</nav>']
        return "\n" + "\n".join(headers)

    def renderWholePage(self):
        return (
            HtmlGeneration.headers + 
            self.renderPageHeader() + 
            '<main class="py-md-5"><div class="container-fluid">' + 
            (card("Invalid Object") if not self.primaryObject() else 
                    self.renderPageBody()) +
            "</div></main>" + 
            HtmlGeneration.footers
            )

    def contextFor(self, entity):
        return self.renderer.contextFor(entity, self.options)
