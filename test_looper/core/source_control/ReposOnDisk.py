import base64
import hashlib
import hmac
import logging
import requests
import simplejson
import traceback
import threading
import os

from test_looper.core.source_control import SourceControl
from test_looper.core.source_control import LocalGitRepo
from test_looper.core.tools.Git import Git

class ReposOnDisk(SourceControl.SourceControl):
    def __init__(self, path_to_repos):
        super(ReposOnDisk, self).__init__()

        assert isinstance(path_to_repos, (str,unicode))

        self.path_to_repos = path_to_repos

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
                path = os.path.join(self.path_to_repos, repoName)
                if not os.path.exists(path):
                    return None
                self.repos[repoName] = LocalGitRepo.LocalGitRepo(path)
            return self.repos[repoName]

    def isAuthorizedForRepo(self, repoName, access_token):
        return True

    def authenticationUrl(self):
        return None

    def authorize_access_token(self, access_token):
        return True

    def getUserNameFromToken(self, access_token):
        return "root"

    def commit_url(self, commit_id):
        return None

    def refresh(self):
        pass