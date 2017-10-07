import base64
import hashlib
import hmac
import logging
import requests
import simplejson

from test_looper.core.TestScriptDefinition import TestScriptDefinition
from test_looper.tools.Git import Git

class LocalGitRepo(Git):
    def __init__(self,
                 path_to_repo,
                 test_definitions_path
                 ):
        super(LocalGitRepo, self).__init__(path_to_repo)
        self.test_definitions_path = test_definitions_path

    def authenticationUrl(self):
        return None

    def getTestScriptDefinitionsForCommit(self, commitId):
        return []

    def authorize_access_token(self, access_token):
        logging.info("LocalGirRepo authorizing dummy access token %s", access_token)
        return True

    def getUserNameFromToken(self, token):
        return "user"

    def commit_url(self, commit):
        return None