import base64
import hashlib
import hmac
import logging
import requests
import simplejson
import traceback
import os

from test_looper.core.TestScriptDefinition import TestScriptDefinition
from test_looper.core.tools.Git import Git

class LocalGitRepo(object):
    def __init__(self,
                 path_to_repo,
                 test_definitions_path
                 ):
        self.path_to_repo = path_to_repo
        self.source_repo = Git(path_to_repo)

        self.test_definitions_path = test_definitions_path

    def authenticationUrl(self):
        return None

    def listBranches(self):
        return [b for b in self.source_repo.listBranches() if 
            not b.startswith("origin/") and not b.startswith("remotes/")]

    def commitsInRevList(self, revlist):
        return self.source_repo.commitsInRevList(revlist)

    def getTestScriptDefinitionsForCommit(self, commitId):
        return self.source_repo.getFileContents(commitId, self.test_definitions_path)

    def authorize_access_token(self, access_token):
        logging.info("LocalGirRepo authorizing dummy access token %s", access_token)
        return True

    def getUserNameFromToken(self, token):
        return "user"

    def commit_url(self, commit):
        return None

    def cloneUrl(self):
        return self.path_to_repo
