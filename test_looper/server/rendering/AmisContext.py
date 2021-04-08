import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import time
import cherrypy


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
        mm = self.testManager.machine_management

        if self.options.get("amiLogs"):
            ami, hash = self.options.get("amiLogs").split("_")
            url = mm.amiConfigLogUrl(ami, hash)
            if url:
                raise cherrypy.HTTPRedirect(url)
            else:
                return HtmlGeneration.card("No logs available")

        if self.options.get("amiSetupScript"):
            ami, hash = self.options.get("amiSetupScript").split("_")
            url = mm.amiConfigLogUrl(ami, hash, "InstallScript")
            if url:
                raise cherrypy.HTTPRedirect(url)
            else:
                return HtmlGeneration.card("No script available")

        osConfigs = set(
            list(mm.windowsOsConfigsAvailable)
            + list(mm.windowsOsConfigsBeingCreated)
            + list(mm.invalidWindowsOsConfigs)
        )

        grid = [["BaseAmi", "Hash", "Status", "", ""]]

        for osConfig in sorted(osConfigs, key=lambda c: (c.ami, c.setupHash)):
            ami, contentHash = osConfig.ami, osConfig.setupHash

            status = (
                "OK"
                if osConfig in mm.windowsOsConfigsAvailable
                else "In progress"
                if osConfig in mm.windowsOsConfigsBeingCreated
                else "Invalid"
            )

            if status in ("OK", "Invalid"):
                logsButton = HtmlGeneration.Link(
                    self.withOptions(amiLogs=ami + "_" + contentHash).urlString(),
                    "Logs",
                    is_button=True,
                    button_style="btn-primary btn-xs",
                ).render()
            else:
                logsButton = ""

            scriptButton = HtmlGeneration.Link(
                self.withOptions(amiSetupScript=ami + "_" + contentHash).urlString(),
                "Setup Script",
                is_button=True,
                button_style="btn-primary btn-xs",
            ).render()

            grid.append([ami, contentHash, status, logsButton, scriptButton])

        return HtmlGeneration.grid(grid)

    def childContexts(self, currentChild):
        return []

    def parentContext(self):
        return self.contextFor("root")

    def renderMenuItemText(self, isHeader):
        return "Images"
