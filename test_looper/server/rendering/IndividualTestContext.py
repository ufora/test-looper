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
    def __init__(self, renderer, context, options):
        Context.Context.__init__(self, renderer, options)
        self.context = context.context
        self.individualTestName = context.individualTestName
        
    def consumePath(self, path):
        return None, path

    def primaryObject(self):
        return ComboContexts.IndividualTest(context=self.context, individualTestName=self.individualTestName)

    def urlBase(self):
        return self.contextFor(self.context).urlBase() + "/-/individualTest/" + self.individualTestName

    def renderBreadcrumbPrefixes(self):
        return ["Tests"]

    def renderLink(self):
        return HtmlGeneration.link(self.individualTestName, self.urlString())

    def relevantTestRuns(self):
        if isinstance(self.context, self.database.Test):
            return self.database.TestRun.lookupAll(test=self.context)
        if isinstance(self.context, self.database.TestRun):
            return [self.context]
        if isinstance(self.context, ComboContexts.CommitAndFilter):
            res = []
            for test in self.context.commit.data.tests.values():
                if self.context.shouldIncludeTest(test):
                    for run in self.database.TestRun.lookupAll(test=test):
                        if run.testNames and self.individualTestName in run.testNames.test_names:
                            res.append(run)
            return res

        if isinstance(self.context, self.database.Commit):
            res = []
            for test in self.context.data.tests.values():
                for run in self.database.TestRun.lookupAll(test=test):
                    if run.testNames and self.individualTestName in run.testNames.test_names:
                        res.append(run)
            return res
        return []

    def renderPageBody(self):
        if self.options.get("context","") == "dropdown-menu":
            items = []
            for testRun in self.relevantTestRuns():
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

            for testRun in [t for t in self.relevantTestRuns() if not t.canceled and t.endTimestamp]:
                timesSeen = -1
                for ix in xrange(len(testRun.testStepNameIndex)):
                    name = testRun.testNames.test_names[testRun.testStepNameIndex[ix]]
                    if name == self.individualTestName:
                        timesSeen += 1

                        pathsAndSizes = self.renderer.artifactStorage.testResultKeysAndSizesForIndividualTest(
                                testRun.test.hash, testRun._identity, self.individualTestName, timesSeen
                                )

                        for path, sz in sorted(pathsAndSizes):
                            grid.append([
                                self.contextFor(testRun).renderLink(False, False),
                                "OK" if testRun.testStepSucceeded[ix] is True else "FAIL",
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
        return self.contextFor(self.context)

    def renderMenuItemText(self, isHeader):
        return (octicon("beaker") if isHeader else "") + self.individualTestName

    def renderNavbarLink(self):
        return self.renderLink()

        
