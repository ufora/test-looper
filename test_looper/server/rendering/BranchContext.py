import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.server.rendering.TestGridRenderer as TestGridRenderer

octicon = HtmlGeneration.octicon
card = HtmlGeneration.card

class BranchContext(Context.Context):
    def __init__(self, renderer, branch, options):
        Context.Context.__init__(self, renderer, options)
        self.branch = branch
        self.repo = branch.repo
        self.reponame = branch.repo.name
        self.branchname = branch.branchname
        self.options = options

    def renderLink(self, includeRepo=True):
        return HtmlGeneration.link(self.branchname, self.urlString())

    def primaryObject(self):
        return self.branch

    def urlBase(self):
        return "repos/" + self.reponame + "/-/branches/" + self.branchname

    def maxCommitCount(self):
        return int(self.options.get("max_commit_count", 100))

    def renderPageBody(self):
        branch = self.branch

        pinGrid = self.pinGridWithUpdateButtons(branch)

        if len(pinGrid) > 1:
            pinContents = (
                HtmlGeneration.grid(pinGrid)
                )
        else:
            pinContents = card("Branch has no pins.")

        commitContents = self.testDisplayForCommits(
            self.testManager.commitsToDisplayForBranch(branch, self.maxCommitCount())
            )

        return HtmlGeneration.tabs("branchtab", [("Commits", commitContents, 'commit'), ("Branch Pins", pinContents, 'pins')])

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
                    c.data.subject.replace("'", "\\'"),
                    c.hash
                    )

            elif len(parentsWeHave) == 1:
                #push a commit onto the branch
                our_branch = branches[(parentsWeHave[0], c.hash)]

                commit_string += "%s.commit({sha1: '%s', message: '%s', detailId: 'commit_%s'});\n" % (
                    our_branch,
                    c.hash, 
                    c.data.subject.replace("'", "\\'"),
                    c.hash
                    )
            else:
                our_branch = branches[(parentsWeHave[0], c.hash)]
                other_branch = branches[(parentsWeHave[1], c.hash)]

                commit_string += "%s.merge(%s, {sha1: '%s', message: '%s', detailId: 'commit_%s'}).delete();" % (other_branch, our_branch, 
                    c.hash, 
                    c.data.subject.replace("'", "\\'"),
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

        gridRenderer = TestGridRenderer.TestGridRenderer(commits, 
            lambda c: [
                t for t in self.testManager.database.Test.lookupAll(commitData=c.data)
                    if not t.testDefinition.matches.Deployment
                ] if c.data else []
            )

        grid = [["COMMIT"] + gridRenderer.headers() + ["", ""]]

        for c in reversed(commits):
            gridrow = self.getBranchCommitRow(c, gridRenderer)

            grid.append(gridrow)

        grid = HtmlGeneration.grid(grid, rowHeightOverride=36)
        
        canvas = HtmlGeneration.gitgraph_canvas_setup(commit_string, grid)

        return detail_divs + canvas

    def getBranchCommitRow(self, commit, renderer):
        row = [self.contextFor(commit).renderLinkWithShaHash()]

        all_tests = self.testManager.database.Test.lookupAll(commitData=commit.data)

        if all_tests:
            row[-1] += "&nbsp;" + self.contextFor(commit).toggleCommitUnderTestLink()
        
        row.extend(renderer.gridRow(commit))
        
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
        lines = [["status", "refname", "Pinned to"]]

        for refname, repoRef in sorted(branch.head.data.repos.iteritems()):
            if repoRef.matches.Pin:
                lines.append(
                    [self.renderPinUpdateLink(branch, refname, repoRef),
                    refname, 
                    self.renderPinReference(refname, repoRef)
                    ])

        return lines
    
    def renderPinUpdateLink(self, branch, reference_name, repoRef):
        if repoRef.auto:
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
