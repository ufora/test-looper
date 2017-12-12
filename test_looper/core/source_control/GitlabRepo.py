import base64
import hashlib
import hmac
import logging
import requests
import simplejson

from test_looper.core.source_control import RemoteRepo
from test_looper.core.TestScriptDefinition import TestScriptDefinition
from test_looper.core.tools.Git import Git

class GitlabRepo(RemoteRepo.RemoteRepo):
    def __init__(self,
                 gitlab,
                 path_to_local_repo,
                 owner,
                 repoName
                 ):
        super(GitlabRepo, self).__init__(repoName, Git(path_to_local_repo))

        self.owner = owner
        self.gitlab = gitlab

    def convertRefToHash(self, branchOrHash):
        hashChar = "0123456789abcdefABCDEF"
        if len(branchOrHash) != 40 or [x for x in branchOrHash if x not in hashChar]:
            return "origin/" + branchOrHash
        return branchOrHash

    def cloneUrl(self):
        return self.gitlab.gitlab_clone_url + ":" + self.owner + "/" + self.name + ".git"

    def listBranches(self):
        return self.source_repo.listBranchesForRemote("origin")

    def commitsBetweenBranches(self, branch, baseline):
        return self.source_repo.commitsInRevList("origin/%s ^origin/%s" % (branch, baseline))

    def getTestScriptDefinitionsForCommit(self, commitHash):
        test_definitions_path = self.source_repo.getTestDefinitionsPath(commitHash)
        if test_definitions_path is None:
            return None

        return self.source_repo.getFileContents(commitHash, test_definitions_path)

    def commit_url(self, commit_id):
        return self.gitlab.gitlab_url + "/%s/%s/commit/%s" % (self.owner, self.repo, commit_id)

    def ensureInitialized(self):
        if not self.source_repo.isInitialized():
            logging.info("Cloning copy of repo %s", self.cloneUrl())
            self.source_repo.cloneFrom(self.cloneUrl())

    def refresh(self):
        self.source_repo.fetchOrigin()