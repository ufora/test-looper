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
from test_looper.core.source_control import GithubRepo
from test_looper.core.TestScriptDefinition import TestScriptDefinition
from test_looper.core.tools.Git import Git

class Github(SourceControl.SourceControl):
    def __init__(self,
                 path_to_local_repos,
                 oauth_key,
                 oauth_secret,
                 access_token,
                 webhook_secret,
                 owner,
                 github_url = "https://github.com",
                 github_login_url = "https://github.com",
                 github_api_url = "https://api.github.com",
                 github_clone_url = "git@github.com",
                 auth_disabled = False
                 ):
        super(Github, self).__init__()

        assert access_token is not None
        assert path_to_local_repos is not None

        self.auth_disabled = auth_disabled

        self.path_to_local_repo_cache = path_to_local_repos

        self.oauth_key = oauth_key
        self.oauth_secret = oauth_secret
        self.access_token = access_token
        self.webhook_secret = webhook_secret
        self.ownerType, self.ownerName = owner.split(":")
        self.github_url = github_url
        self.github_api_url = github_api_url
        self.github_login_url = github_login_url
        self.github_clone_url = github_clone_url
        self.lock = threading.Lock()
        self.repos = {}

    def shouldVerify(self):
        return self.github_url == "https://github.com"

    def listRepos(self):
        url = self.github_api_url + '/%ss/%s/repos' % (self.ownerType, self.ownerName)

        response = requests.get(
            url,
            headers={
                'accept': 'application/json'
                },
            verify=self.shouldVerify()
            )

        res = []
        try:
            for r in simplejson.loads(response.content):
                res.append(r["name"])
        except:
            logging.error(traceback.format_exc())
            return []

        return res

    def getRepo(self, repoName):
        with self.lock:
            if repoName not in self.repos:
                path = os.path.join(self.path_to_local_repo_cache, repoName)
                self.repos[repoName] = GithubRepo.GithubRepo(self, path, self.ownerName, repoName)

            repo = self.repos[repoName]

        repo.ensureInitialized()

        return repo

    def verify_webhook_request(self, headers, payload):
        if not self.auth_disabled:
            signature = "sha1=" + hmac.new(self.webhook_secret, body, hashlib.sha1).hexdigest()
            if ('X-GitHub-Event' not in headers or 'X-HUB-SIGNATURE' not in headers or
                    headers['X-HUB-SIGNATURE'] != signature):
                return None

        if headers['X-GitHub-Event'] != 'push':
            return {}

        return {
            'branch': payload['ref'].split('/')[-1],
            'repo': payload['repository']['name']
            }

    ###########
    ## OAuth
    def authenticationUrl(self):
        if self.auth_disabled:
            return None

        """Return the url to which we should direct unauthorized users"""
        return self.github_login_url + "/login/oauth/authorize?scope=read:org&client_id=" + \
            self.oauth_key


    def getAccessTokenFromAuthCallbackCode(self, code):
        response = requests.post(
            self.github_login_url + '/login/oauth/access_token',
            headers={
                'accept': 'application/json'
                },
            data={
                'client_id': self.oauth_key,
                'client_secret': self.oauth_secret,
                'code': code
                },
            verify=self.shouldVerify()
            )

        result = simplejson.loads(response.text)

        if 'access_token' not in result:
            logging.error("didn't find 'access_token' in %s", response.text)

        return result['access_token']

    ## OAuth
    ###########


    def authorize_access_token(self, access_token):
        if self.auth_disabled:
            return True

        logging.info("Checking access token %s", access_token)

        response = requests.get(
            self.github_api_url + "/applications/%s/tokens/%s" % (
                self.oauth_key, access_token
                ),
            auth=requests.auth.HTTPBasicAuth(self.oauth_key,
                                             self.oauth_secret),
            verify=self.shouldVerify()
            )
        if not response.ok:
            logging.info(
                "Denying access for token %s because we can't get the user name",
                access_token
                )
            return False

        user = simplejson.loads(response.text)
        if not 'user' in user or not 'login' in user['user']:
            logging.info(
                "Denying access for token %s because auth response didn't include user info",
                access_token
                )
            return False

        response = requests.get(
            self.github_api_url + "/orgs/%s/members/%s?access_token=%s" % (
                self.ownerName,
                user['user']['login'],
                access_token
                ),
            verify=self.shouldVerify()
            )
        if response.status_code == 204:
            return True

        logging.info(
            "Denying access for user %s because they are not a member of the %s owner",
            user['user']['login'],
            self.owner
            )
        return False

    def getUserNameFromToken(self, access_token):
        """Given a github access token, find out what user the token is assigned to."""
        if self.auth_disabled:
            return "user"

        return simplejson.loads(
            requests.get(self.github_api_url + "/user?access_token=" + access_token, verify=self.shouldVerify()).text
            )['login']


    def commit_url(self, commit_id):
        repoName, commitHash = commit_id.split("/")
        return self.github_url + "/%s/%s/commit/%s" % (self.ownerName, repoName, commitHash)

    def refresh(self):
        for r in self.repos.values():
            r.refresh()
