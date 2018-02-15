import test_looper.server.rendering.Context as Context
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.HtmlGeneration as HtmlGeneration
import urllib
import cgi
import time
import uuid

octicon = HtmlGeneration.octicon
card = HtmlGeneration.card

class CommitContext(Context.Context):
    def __init__(self, renderer, commit, options):
        Context.Context.__init__(self, renderer, options)
        self.reponame = commit.repo.name
        self.commitHash = commit.hash
        self.options = options

        self.repo = commit.repo
        self.commit = commit        
        self.nameInBranch = None

        if self.commit:
            self.branch, self.nameInBranch = self.testManager.bestCommitBranchAndName(self.commit)

            if self.nameInBranch is None:
                self.nameInBranch = commitHash[:10]
        else:
            self.branch = None


    def consumePath(self, path):
        if path:
            testpath, remainder = self.popToDash(path)

            fullname = self.commit.repo.name + "/" + self.commit.hash + "/" + "/".join(testpath)

            test = self.database.Test.lookupAny(fullname=fullname)

            if not test:
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

    
    def recency(self):
        return '<span class="text-muted">%s</span>' % (HtmlGeneration.secondsUpToString(time.time() - self.commit.data.timestamp) + " ago")

    def renderLinkWithShaHash(self):
        if not self.commit.data:
            return ''

        return octicon("git-commit") + HtmlGeneration.link(
                "<code>" + self.commit.hash[:8] + "</code>",
                self.urlString(),
                hover_text=("commit " + self.commit.hash[:10] + " : " + ("" if not self.commit.data else self.commit.data.commitMessage))
                )

    def renderSubjectAndAuthor(self, maxChars=40):
        if not self.commit.data:
            return ""

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

    def renderLinkWithSubject(self, maxChars=40):
        if not self.commit.data:
            return ""

        return (
            self.renderLinkWithShaHash() + 
            "&nbsp;" +
            self.renderSubjectAndAuthor(maxChars)
            )

    def renderContentCallout(self):
        detail_header = "Commit Info"

        header,body = (self.commit.data.commitMessage + "\n").split("\n",1)

        header = header.strip()
        body = body.strip()

        detail = """
            <div>Author: {author} &lt;{author_email}&gt;</div>
            <div>Date: {timestamp}</div>
            <h3>{header}</h3>
            <pre>{body}</pre>
            """.format(
                header=cgi.escape(header),
                body=cgi.escape(body), 
                author=self.commit.data.author, 
                author_email=self.commit.data.authorEmail,
                timestamp=time.asctime(time.gmtime(self.commit.data.timestamp))
                )

        return HtmlGeneration.popover(contents=octicon("comment"), detail_title=detail_header, detail_view=detail, width=400, data_placement="right") 

    def renderLink(self, includeRepo=True, includeBranch=True):
        res = ""
        if includeRepo:
            assert includeBranch
            res += self.contextFor(self.repo).renderLink()

        if includeBranch:
            if res:
                res += "/"
            res += self.contextFor(self.branch).renderLink(includeRepo=False)

        name = self.nameInBranch
        if not name:
            name = "/HEAD"
        else:
            if len(name) < 5:
                name += "&nbsp;" * max(0, 5 - len(name))

        return res + HtmlGeneration.link(name, self.urlString())

    def primaryObject(self):
        return self.commit

    def urlBase(self):
        return "repos/" + self.reponame + "/-/commits/" + self.commitHash

    def renderPageBody(self):
        if not self.commit.data:
            return card("Commit hasn't been imported yet")

        commitTestGrid = self.commitTestGrid()

        return HtmlGeneration.tabs("commit", [
            ("Tests", commitTestGrid, "commit_tests"),
            ("Test Definitions", self.commitTestDefinitionsInfo(), "commit_test_defs")
            ])

    def commitTestDefinitionsInfo(self):
        raw_text, extension = self.testManager.getRawTestFileForCommit(self.commit)

        if raw_text:
            return card('<pre class="language-yaml"><code class="line-numbers">%s</code></pre>' % cgi.escape(raw_text))
        else:
            return card("No test definitions found")

    def commitTestGrid(self):
        commit = self.commit

        tests = self.testManager.database.Test.lookupAll(commitData=commit.data)
        
        if not tests:
            if commit.data.noTestsFound:
                return card("Commit defined no test definition file.")

            raw_text, extension = self.testManager.getRawTestFileForCommit(commit)
            if not raw_text:
                return card("Commit defined no tests because the test-definitions file is empty.")
            else:
                return card("<div>Commit defined no tests. Maybe look at the test definitions? Error was</div><pre><code>%s</code></pre>" % commit.data.testDefinitionsError)

        tests = sorted(tests, key=lambda test: test.fullname)
        
        grid = [["TEST", "", "", "ENVIRONMENT", "RUNNING", "COMPLETED", "FAILED", "PRIORITY", "AVG_TEST_CT", "AVG_FAILURE_CT", "AVG_RUNTIME", "", "TEST_DEPS"]]

        for t in tests:
            row = []

            row.append(
                self.contextFor(t).renderLink(includeCommit=False)
                )
            row.append("") #self.clearTestLink(t.fullname))
            row.append(
                HtmlGeneration.Link(self.contextFor(t).bootTestOrEnvUrl(),
                   "BOOT",
                   is_button=True,
                   new_tab=True,
                   button_style=self.renderer.disable_if_cant_write('btn-primary btn-xs')
                   )
                )

            row.append(t.testDefinition.environment_name)

            row.append(str(t.activeRuns))
            row.append(str(t.totalRuns))
            row.append(str(t.totalRuns - t.successes))

            def stringifyPriority(calculatedPriority, priority):
                if priority.matches.HardwareComboUnbootable:
                    return "HardwareComboUnbootable"
                if priority.matches.WaitingOnBuilds:
                    return "WaitingOnBuilds"
                if priority.matches.UnresolvedDependencies:
                    return "UnresolvedTestDependencies"
                if priority.matches.NoMoreTests:
                    return "HaveEnough"
                if priority.matches.DependencyFailed:
                    return "DependencyFailed"
                if (priority.matches.WantsMoreTests or priority.matches.FirstTest or priority.matches.FirstBuild):
                    return "WaitingForHardware"
                if priority.matches.WaitingToRetry:
                    return "WaitingToRetry"

                return "Unknown"

            row.append(stringifyPriority(t.calculatedPriority, t.priority))

            all_tests = list(self.testManager.database.TestRun.lookupAll(test=t))
            all_noncanceled_tests = [testRun for testRun in all_tests if not testRun.canceled]
            finished_tests = [testRun for testRun in all_noncanceled_tests if testRun.endTimestamp > 0.0]

            if t.totalRuns:
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
        return TestSummaryRenderer.TestSummaryRenderer(self.testManager.allTestsDependedOnByTest(t)).renderSummary()

