import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import time

class AmisContext(Context.Context):
    def __init__(self, renderer, options):
        Context.Context.__init__(self, renderer, options)
        self.options = options

    def consumePath(self, path):
        return None, path

    def renderLink(self):
        return HtmlGeneration.link("Images", self.urlString())

    def primaryObject(self):
        return "amis"

    def urlBase(self):
        return "amis"

    def renderPageBody(self):
        amisAndHashes = self.testManager.machine_management.api.listWindowsOsConfigs()

        grid = [["BaseAmi", "Hash", "Status"]]

        for ami,contentHash in sorted(amisAndHashes):
            status = amisAndHashes[ami,contentHash]

            grid.append([ami,contentHash,status])
            
        return HtmlGeneration.grid(grid)

    def childContexts(self, currentChild):
        return []

    def parentContext(self):
        return self.contextFor("root")
        
    def renderMenuItemText(self, isHeader):
        return "Images"

