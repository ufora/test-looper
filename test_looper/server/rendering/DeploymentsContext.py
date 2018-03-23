import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import time

secondsUpToString = HtmlGeneration.secondsUpToString

class DeploymentsContext(Context.Context):
    def __init__(self, renderer, options):
        Context.Context.__init__(self, renderer, options)
        self.options = options

    def consumePath(self, path):
        return None, path

    def renderLink(self):
        return HtmlGeneration.link("Deployments", self.urlString())

    def primaryObject(self):
        return "deployments"

    def urlBase(self):
        return "deployments"

    def renderPageBody(self):
        deployments = sorted(
            self.testManager.database.Deployment.lookupAll(isAlive=True),
            key=lambda d:d.createdTimestamp
            )
        
        grid = [["COMMIT", "TEST", "BOOTED AT", "UP FOR", "CLIENTS", "", ""]]

        for d in deployments:
            row = []

            commit = self.testManager.oldestCommitForTest(d.test)
            repo = commit.repo

            row.append(self.contextFor(commit).renderLink())

            row.append(d.test.testDefinition.name)

            row.append(time.asctime(time.gmtime(d.createdTimestamp)))

            row.append(secondsUpToString(time.time() - d.createdTimestamp))

            row.append(str(self.testManager.streamForDeployment(d._identity).clientCount()))

            row.append(self.connectDeploymentLink(d))

            row.append(self.shutdownDeploymentLink(d))

            grid.append(row)

        return HtmlGeneration.grid(grid)

    def connectDeploymentLink(self, d):
        return HtmlGeneration.Link( 
            "/terminalForDeployment?deploymentId=" + d._identity,
            "connect",
            is_button=True,
            new_tab=True,
            button_style='btn-primary btn-xs'
            )

    def shutdownDeploymentLink(self, d):
        return HtmlGeneration.Link( 
            "/shutdownDeployment?deploymentId=" + d._identity,
            "shutdown",
            is_button=True,
            new_tab=True,
            button_style='btn-primary btn-xs'
            )
    
    def childContexts(self, currentChild):
        return []
    
    def parentContext(self):
        return self.contextFor("root")

    def renderMenuItemText(self, isHeader):
        return "Deployments"
