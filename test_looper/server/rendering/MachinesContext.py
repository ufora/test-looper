import test_looper.server.rendering.Context as Context
import test_looper.server.HtmlGeneration as HtmlGeneration
import time

class MachinesContext(Context.Context):
    def __init__(self, renderer, options):
        Context.Context.__init__(self, renderer, options)
        self.options = options

    def consumePath(self, path):
        return None, path

    def renderLink(self):
        return HtmlGeneration.link("Machines", self.urlString())

    def primaryObject(self):
        return "machines"

    def urlBase(self):
        return "machines"

    def renderPageBody(self):
        machines = self.testManager.database.Machine.lookupAll(isAlive=True)

        grid = [["MachineID", "Hardware", "OS", "UP FOR", "STATUS", "LASTMSG", "COMMIT", "TEST", "LOGS", "CANCEL", ""]]
        for m in sorted(machines, key=lambda m: -m.bootTime):
            row = []
            row.append(m.machineId)
            row.append("%s cores, %s GB" % (m.hardware.cores, m.hardware.ram_gb))
            if m.os.matches.WindowsVM:
                row.append("Win(%s)" % m.os.ami)
            elif m.os.matches.LinuxVM:
                row.append("Linux(%s)" % m.os.ami)
            elif m.os.matches.LinuxWithDocker:
                row.append("LinuxDocker()")
            elif m.os.matches.WindowsWithDocker:
                row.append("WindowsDocker()")
            else:
                row.append("Unknown")

            row.append(HtmlGeneration.secondsUpToString(time.time() - m.bootTime))
            
            if m.firstHeartbeat < 1.0:
                row.append('<span class="octicon octicon-watch" aria-hidden="true"></span>')
            elif time.time() - m.lastHeartbeat < 60:
                row.append('<span class="octicon octicon-check" aria-hidden="true"'
                    + ' data-toggle="tooltip" data-placement="right" title="Heartbeat %s seconds ago" ' % (int(time.time() - m.lastHeartbeat))
                    + '></span>'
                    )
            else:
                row.append('<span class="octicon octicon-issue-opened" aria-hidden="true"'
                    + ' data-toggle="tooltip" data-placement="right" title="Heartbeat %s seconds ago" ' % (int(time.time() - m.lastHeartbeat))
                    + '></span>'
                    )
            
            row.append(m.lastHeartbeatMsg)

            tests = self.testManager.database.TestRun.lookupAll(runningOnMachine=m)
            deployments = self.testManager.database.Deployment.lookupAll(runningOnMachine=m)

            if len(tests) + len(deployments) > 1:
                row.append("ERROR: multiple test runs/deployments")
            elif tests:
                commit = tests[0].test.commitData.commit
                try:
                    row.append(self.contextFor(commit).renderLink())
                except:
                    row.append("")

                row.append(self.renderer.testRunLink(tests[0], tests[0].test.testDefinition.name))
                row.append(self.renderer.testLogsButton(tests[0]._identity))
                row.append(self.renderer.cancelTestRunButton(tests[0]._identity))
                
            elif deployments:
                commit = deployments[0].test.commitData.commit
                try:
                    row.append(self.contextFor(commit).renderLink())
                except:
                    row.append("")

                d = deployments[0]
                row.append("DEPLOYMENT")
                row.append(self.renderer.connectDeploymentLink(d))
                row.append(self.renderer.shutdownDeploymentLink(d))
            
            grid.append(row)
            
        return HtmlGeneration.grid(grid)
