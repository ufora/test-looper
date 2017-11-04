import base64
import hashlib
import hmac
import logging
import requests
import simplejson
import traceback
import os

from test_looper.core.TestScriptDefinition import TestScriptDefinition
from test_looper.core.source_control import SourceControl
from test_looper.core.source_control import RemoteRepo
from test_looper.core.tools.Git import Git

class LocalGitRepo(RemoteRepo.RemoteRepo):
    def __init__(self,
                 path_to_repo
                 ):
        super(LocalGitRepo, self).__init__(os.path.split(path_to_repo)[1])

        assert isinstance(path_to_repo, (str,unicode))

        self.path_to_repo = str(path_to_repo)
        self.source_repo = Git(self.path_to_repo)

    def listBranches(self):
        print "Branches are ", self.source_repo.listBranches(), " in ", self.path_to_repo
        return [b for b in self.source_repo.listBranches() if 
            not b.startswith("origin/") and not b.startswith("remotes/")]

    def commitsBetweenCommitIds(self, c1, c2):
        return self.source_repo.commitsInRevList(c1 + " ^" + c2)
        
    def commitsBetweenBranches(self, branch, baseline):
        return self.source_repo.commitsInRevList("%s ^%s" % (branch, baseline))

    def getTestScriptDefinitionsForCommit(self, commitId):
        path = self.source_repo.getTestDefinitionsPath(commitId)

        if path is None:
            return None

        return self.source_repo.getFileContents(commitId, path)

    def commit_url(self, commit):
        return None

    def cloneUrl(self):
        return self.path_to_repo
