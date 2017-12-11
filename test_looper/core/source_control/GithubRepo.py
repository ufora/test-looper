import base64
import hashlib
import hmac
import logging
import requests
import simplejson
import os

from test_looper.core.source_control import RemoteRepo
from test_looper.core.tools.Git import Git

class GithubRepo(RemoteRepo.RemoteRepo):
    def __init__(self,
                 github,
                 path_to_local_repo,
                 owner,
                 repoName
                 ):
        super(GithubRepo, self).__init__(repoName, Git(path_to_local_repo))

        self.owner = owner
        self.github = github

    def cloneUrl(self):
        return self.github.github_clone_url + ":" + self.owner + "/" + self.name + ".git"

    def listBranches(self):
        return self.source_repo.listBranchesForRemote("origin")

    def commitsBetweenBranches(self, branch, baseline):
        return self.source_repo.commitsInRevList("origin/%s ^origin/%s" % (branch, baseline))

    def getTestScriptDefinitionsForCommit(self, commitHash):
        test_definitions_path = self.source_repo.getTestDefinitionsPath(commitHash)
        
        if test_definitions_path is None:
            return None, None

        return self.source_repo.getFileContents(commitHash, test_definitions_path), os.path.splitext(test_definitions_path)[1]

    def commit_url(self, commit_id):
        return self.github.github_url + "/%s/%s/commit/%s" % (self.owner, self.repo, commit_id)

    def ensureInitialized(self):
        if not self.source_repo.isInitialized():
            logging.info("Cloning copy of repo %s", self.cloneUrl())
            self.source_repo.cloneFrom(self.cloneUrl())

    def refresh(self):
        self.source_repo.fetchOrigin()