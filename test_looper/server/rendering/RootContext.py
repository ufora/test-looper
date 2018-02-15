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
        assert False

    def primaryObject(self):
        return None

    def urlBase(self):
        return "repos/" + self.reponame

    def childContexts(self, currentChild):
        return [self.contextFor(x) for x in ["repos", "deployments", "machines"]]

    def parentContext(self):
        return None
