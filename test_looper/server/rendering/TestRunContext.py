import test_looper.server.rendering.Context as Context
import test_looper.server.rendering.TestSummaryRenderer as TestSummaryRenderer
import test_looper.server.HtmlGeneration as HtmlGeneration
import logging

card = HtmlGeneration.card

class TestRunContext(Context.Context):
    def __init__(self, renderer, testRun, options):
        Context.Context.__init__(self, renderer, options)
        self.testRun = testRun
        self.test = self.testRun.test
        self.commit = self.test.commitData.commit
        self.repo = self.commit.repo

    def consumePath(self, path):
        return None, path

    def primaryObject(self):
        return self.testRun

    def urlBase(self):
        prefix = "repos/" + self.reponame + "/-/commits/"
        return prefix + self.commit.hash + "/" + self.test.testDefinition.name + "/-/runs/" + self.test._identity

    def renderLink(self, includeCommit=True):
        if includeCommit:
            res = self.contextFor(self.commit).renderLink() + "/"
        else:
            res = ''

        return res + HtmlGeneration.link(self.testName, self.urlString())

    def renderPageBody(self):
        artifacts = self.artifactsForTestRunGrid()

        individualTestReport = self.individualTestReport()

        return HtmlGeneration.tabs("test_tab", [
                ("Artifacts", artifacts, "artifacts"),
                ("Individual Tests", individualTestReport, "tests_individual")
                ]
                )

    def individualTestReport(self):
        testRun = self.testRun

        if testRun.totalTestCount:
            individual_tests_grid = [["TEST_NAME", "PASSED"]]
            pass_dict = {}

            for ix in xrange(len(testRun.testNames.test_names)):
                pass_dict[testRun.testNames.test_names[ix]] = "PASS" if testRun.testFailures[ix] else "FAIL"

            for k,v in sorted(pass_dict.items()):
                individual_tests_grid.append((k,v))

            return HtmlGeneration.grid(individual_tests_grid)
        else:
            return card("No Individual Tests Reported")

    def artifactsForTestRunGrid(self):
        testRun = self.testRun

        grid = [["Artifact", "Size"]]

        commit = testRun.test.commitData.commit

        if testRun.test.testDefinition.matches.Build:
            build_key = testRun.test.testDefinition.name.replace("/","_") + ".tar.gz"

            if self.renderer.artifactStorage.build_exists(commit.repo.name, commit.hash, build_key):
                grid.append([
                    HtmlGeneration.link(build_key, self.renderer.buildDownloadUrl(commit.repo.name, commit.hash, build_key)),
                    bytesToHumanSize(self.renderer.artifactStorage.build_size(commit.repo.name, commit.hash, build_key))
                    ])
            else:
                logging.info("No build found at %s", build_key)

        for artifactName, sizeInBytes in self.renderer.artifactStorage.testResultKeysForWithSizes(commit.repo.name, commit.hash, testRun._identity):
            grid.append([
                HtmlGeneration.link(
                    artifactName,
                    self.renderer.testResultDownloadUrl(testRun._identity, artifactName)
                    ),
                bytesToHumanSize(sizeInBytes)
                ])

        if not grid:
            return card("No Test Artifacts produced")

        return HtmlGeneration.grid(grid)

