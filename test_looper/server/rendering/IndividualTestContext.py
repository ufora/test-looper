import test_looper.server.rendering.Context as Context
import test_looper.server.rendering.ComboContexts as ComboContexts
import test_looper.server.HtmlGeneration as HtmlGeneration
import test_looper.core.algebraic_to_json as algebraic_to_json
import time
import cgi
import cherrypy
import os

octicon = HtmlGeneration.octicon
card = HtmlGeneration.card

class IndividualTestContext(Context.Context):
    def __init__(self, renderer, individualTest, options):
        Context.Context.__init__(self, renderer, options)
        self.test = individualTest.test
        self.individualTestName = individualTest.individualTestName

        self.commit = self.testManager.oldestCommitForTest(self.test)
        self.repo = self.commit.repo
        self.testName = self.test.testDefinitionSummary.name
        
    def consumePath(self, path):
        return None, path

    def primaryObject(self):
        return ComboContexts.IndividualTest(test=self.test, individualTestName=self.individualTestName)

    def urlBase(self):
        prefix = "repos/" + self.repo.name + "/-/commits/"
        return prefix + self.commit.hash + "/tests/" + self.testName + "/-/test/" + self.individualTestName

    def renderBreadcrumbPrefixes(self):
        return ["Tests"]

    def renderLink(self, includeCommit=True, includeTest=True):
        res = ""
        if includeCommit:
            res += self.contextFor(self.commit).renderLink()

        if includeTest:
            if res:
                res += "/"
            res += HtmlGeneration.link(self.testName, self.contextFor(self.test).urlString())

        if res:
            res += '/'

        return res + HtmlGeneration.link(self.individualTestName, self.urlString())

    def renderPageBody(self):
        if self.options.get("context","") == "dropdown-menu":
            items = []
            for testRun in self.database.TestRun.lookupAll(test=self.test):
                for path, sz in self.renderer.artifactStorage.testResultKeysAndSizesForIndividualTest(
                        testRun.test.hash, testRun._identity, self.individualTestName
                        ):
                    contents = os.path.basename(path) + " (" + HtmlGeneration.bytesToHumanSize(sz) + ")"
                    if sz:
                        items.append(
                            '<a class="dropdown-item" href="{link}" title="{title}">{contents}</a>'.format(
                                link=self.renderer.testResultDownloadUrl(testRun._identity, path),
                                title=os.path.basename(path),
                                contents=contents
                                )
                            )
                    else:
                        items.append('<span class="dropdown-item disabled text-muted">{contents}</span>'.format(contents=contents))
            return "".join(items)
        else:
            grid = [["Test Run", "Failure", "File", "Size"]]

            for testRun in [t for t in self.database.TestRun.lookupAll(test=self.test) if not t.canceled and t.endTimestamp]:
                try:
                    index = testRun.testNames.test_names.index(self.individualTestName)
                    passFail = True if testRun.testFailures[index] else False
                except:
                    passFail = None

                if passFail is not None:
                    pathsAndSizes = self.renderer.artifactStorage.testResultKeysAndSizesForIndividualTest(
                            testRun.test.hash, testRun._identity, self.individualTestName
                            )

                    for path, sz in pathsAndSizes:
                        grid.append([
                            self.contextFor(testRun).renderLink(False, False),
                            "OK" if passFail is True else "FAIL" if passFail is False else "",
                            HtmlGeneration.link(
                                os.path.basename(path), 
                                self.renderer.testResultDownloadUrl(testRun._identity, path)
                                ),
                            HtmlGeneration.bytesToHumanSize(sz)
                            ])

                    if not pathsAndSizes:
                        grid.append([
                            self.contextFor(testRun).renderLink(False, False),
                            "FAIL" if passFail is True else "OK" if passFail is False else "",
                            '<span class="text-muted">%s</span>' % "No artifacts",
                            ""
                            ])

            return HtmlGeneration.grid(grid,dataTables=True)

    def childContexts(self, currentChild):
        return []

    def parentContext(self):
        return self.contextFor(self.test)


    def renderMenuItemText(self, isHeader):
        return (octicon("beaker") if isHeader else "") + self.testName

    def renderNavbarLink(self):
        return self.renderLink(includeCommit=False, includeTest=False)

        
