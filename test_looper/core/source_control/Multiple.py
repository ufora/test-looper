import logging

from test_looper.core.source_control import SourceControl


class Multiple(SourceControl.SourceControl):
    def __init__(self, serverToControlMap):
        self.serverToControlMap = serverToControlMap

    def isWebhookInstalled(self, reponame, server_ports):
        serverName, subRepoName = reponame.split("/", 1)

        return self.serverToControlMap[serverName].isWebhookInstalled(
            subRepoName, server_ports
        )

    def installWebhook(self, reponame, server_ports):
        serverName, subRepoName = reponame.split("/", 1)

        return self.serverToControlMap[serverName].installWebhook(
            subRepoName, server_ports
        )

    def listRepos(self):
        repos = []

        for serverName, sourceControl in self.serverToControlMap.items():
            repos.extend(
                [
                    serverName + "/" + subRepoName
                    for subRepoName in sourceControl.listRepos()
                ]
            )

        return repos

    def authenticationUrl(self):
        return None

    def getRepo(self, repoName):
        serverName, subRepoName = repoName.split("/", 1)

        return self.serverToControlMap[serverName].getRepo(subRepoName)

    def isAuthorizedForRepo(self, repoName, access_token):
        serverName, subRepoName = repoName.split("/", 1)

        return self.serverToControlMap[serverName].isAuthorizedForRepo(
            subRepoName, access_token
        )

    def commit_url(self, repoName, commitHash):
        serverName, subRepoName = repoName.split("/", 1)

        return self.serverToControlMap[serverName].commit_url(repoName, commitHash)

    def authorize_access_token(self, access_token):
        return True

    def getUserNameFromToken(self, access_token):
        return "user"

    def refresh(self):
        for scm in self.serverToControlMap.values():
            scm.refresh()

    def verify_webhook_request(self, headers, payload):
        for name, sourceControl in self.serverToControlMap.items():
            res = sourceControl.verify_webhook_request(headers, payload)
            if res:
                return dict(repo=name + "/" + res["repo"], branch=res["branch"])

        return None
