import base64
import hashlib
import hmac
import logging
import requests
import simplejson
import traceback
import os

from test_looper.core.source_control import SourceControl
from test_looper.core.source_control import RemoteRepo
from test_looper.core.tools.Git import Git

class LocalGitRepo(RemoteRepo.RemoteRepo):
    def __init__(self,
                 path_to_repo
                 ):
        assert isinstance(path_to_repo, (str,unicode))

        self.path_to_repo = str(path_to_repo)
        
        source_repo = Git(self.path_to_repo)

        super(LocalGitRepo, self).__init__(os.path.split(path_to_repo)[1], source_repo)


    def listBranches(self):
        print "Branches are ", self.source_repo.listBranches(), " in ", self.path_to_repo
        return [b for b in self.source_repo.listBranches() if 
            not b.startswith("origin/") and not b.startswith("remotes/")]

    def getTestScriptDefinitionsForCommit(self, commitHash):
        path = self.source_repo.getTestDefinitionsPath(commitHash)

        if path is None:
            return None, None

        return self.source_repo.getFileContents(commitHash, path), os.path.splitext(path)[1]

    def commit_url(self, commit):
        return None

    def cloneUrl(self):
        return self.path_to_repo

    def refresh(self):
        self.source_repo.fetchOrigin()