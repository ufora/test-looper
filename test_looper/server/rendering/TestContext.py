import test_looper.server.rendering.Context as Context
import test_looper.server.rendering.TestRunsContext as TestRunsContext
import test_looper.server.HtmlGeneration as HtmlGeneration

class TestContext(Context.Context):
    def __init__(self, renderer, test, options):
        Context.Context.__init__(self, renderer, options)
        self.test = test
        self.commit = test.commitData.commit
        self.repo = self.commit.repo
        self.testName = test.testDefinition.name

    def consumePath(self, path):
        if path and path[0] == "runs":
            return TestRunsContext.TestRunsContext(self.renderer, self.test, self.options), path[1:]

        return None, path

    def primaryObject(self):
        return self.test

    def urlBase(self):
        prefix = "repos/" + self.repo.name + "/-/commits/"
        return prefix + self.commit.hash + "/" + self.testName

    def renderLink(self, includeCommit=True):
        if includeCommit:
            res = self.contextFor(self.commit).renderLink() + "/"
        else:
            res = ''

        return res + HtmlGeneration.link(self.testName, self.urlString())

    def bootTestOrEnvUrl(self):
        return self.urlString(action="boot")

    def renderPageBody(self):
        return TestRunsContext.TestRunsContext(self.renderer, self.test, self.options).renderPageBody()

