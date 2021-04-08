import base64
import hashlib
import hmac
import logging
import requests
import json
import traceback
import threading
import os

from test_looper.core.source_control import SourceControl
from test_looper.core.tools.Git import Git
import test_looper.core.source_control.RemoteRepo as RemoteRepo

class ReposOnDisk(SourceControl.SourceControl):
    def __init__(self, path_to_local_repo_cache, config):
        super(ReposOnDisk, self).__init__()

        assert isinstance(config.path_to_repos, str)

        self.path_to_local_repo_cache = str(path_to_local_repo_cache)
        self.path_to_repos = str(config.path_to_repos)

        self.repos = {}

        self.lock = threading.Lock()

    def listRepos(self):
        if not os.path.exists(self.path_to_repos):
            logging.warn("Repos directory %s doesn't exist", self.path_to_repos)
            return []
        return os.listdir(self.path_to_repos)

    def getRepo(self, repoName):
        with self.lock:
            if repoName not in self.repos:
                path = os.path.join(self.path_to_local_repo_cache, repoName)

                if not os.path.exists(os.path.join(self.path_to_repos, repoName)):
                    return None
                
                self.repos[repoName] = RemoteRepo.RemoteRepo(repoName, path, self)

                self.repos[repoName].ensureInitialized()

            return self.repos[repoName]

    def isAuthorizedForRepo(self, repoName, access_token):
        return True

    def authenticationUrl(self):
        return None

    def authorize_access_token(self, access_token):
        return True

    def getUserNameFromToken(self, access_token):
        return "root"

    def commit_url(self, repoName, commitHash):
        return None

    def cloneUrl(self, repoName):
        return "file://%s/%s" % (self.path_to_repos, repoName)

    def refresh(self):
        pass
