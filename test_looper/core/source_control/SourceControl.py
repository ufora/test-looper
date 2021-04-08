"""Represents a collection of repos provided by a service such as Github, or local disk."""
import logging


class SourceControl(object):
    def __init__(self):
        pass

    def isWebhookInstalled(self, reponame, server_ports):
        return False

    def installWebhook(self, reponame, server_ports):
        pass

    def listRepos(self):
        assert False, "Subclasses Implement"

    def getRepo(self, repoName):
        """Return the named RemoteRepo object."""
        assert False, "Subclasses Implement"

    def isAuthorizedForRepo(self, repoName, access_token):
        assert False, "Subclasses Implement"

    def listBranches(self):
        res = []
        repos = self.listRepos()
        logging.info("Listing repos: %s", repos)
        for r in repos:
            branchNames = self.getRepo(r).listBranches()
            logging.info("Branches of %s are %s", r, branchNames)

            for b in branchNames:
                res.append(r + "/" + b)
        return res
