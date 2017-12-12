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
from test_looper.core.source_control import GitlabRepo
from test_looper.core.TestScriptDefinition import TestScriptDefinition
from test_looper.core.tools.Git import Git

class Gitlab(SourceControl.SourceControl):
    def __init__(self,
                 path_to_local_repos,
                 oauth_key,
                 oauth_secret,
                 private_token,
                 webhook_secret,
                 owner,
                 gitlab_url = "https://gitlab.com",
                 gitlab_login_url = "https://gitlab.com",
                 gitlab_api_url = "https://api.gitlab.com",
                 gitlab_clone_url = "git@gitlab.com",
                 auth_disabled = False
                 ):
        super(Gitlab, self).__init__()

        assert private_token is not None
        assert path_to_local_repos is not None

        self.auth_disabled = auth_disabled

        self.path_to_local_repo_cache = path_to_local_repos

        self.oauth_key = oauth_key
        self.oauth_secret = oauth_secret
        self.private_token = private_token
        self.webhook_secret = webhook_secret
        self.owner = owner
        self.gitlab_url = gitlab_url
        self.gitlab_api_url = gitlab_api_url
        self.gitlab_login_url = gitlab_login_url
        self.gitlab_clone_url = gitlab_clone_url
        self.lock = threading.Lock()
        self.repos = {}

    def shouldVerify(self):
        return True

    def listRepos(self):
        url = self.gitlab_api_url + '/projects?private_token=%s&owner=%s' % (self.private_token, self.owner)

        headers={'accept': 'application/json'}
        
        response = requests.get(
            url,
            headers=headers,
            verify=self.shouldVerify()
            )

        res = []
        try:
            for r in simplejson.loads(response.content):
                try:
                    if r['namespace']['full_path'] == self.owner:
                        res.append(r["name"])
                except:
                    logging.error("failed with: %s", r)
                    logging.error(traceback.format_exc())
        except:
            logging.error("GOT: %s", response.content)
            logging.error(traceback.format_exc())
            return []

        return res

    def getRepo(self, repoName):
        with self.lock:
            if repoName not in self.repos:
                path = os.path.join(self.path_to_local_repo_cache, repoName)
                self.repos[repoName] = GitlabRepo.GitlabRepo(self, path, self.owner, repoName)

            repo = self.repos[repoName]

        repo.ensureInitialized()

        return repo

    def verify_webhook_request(self, headers, payload):
        if not self.auth_disabled:
            signature = "sha1=" + hmac.new(self.webhook_secret, body, hashlib.sha1).hexdigest()
            if ('X-GitHub-Event' not in headers or 'X-HUB-SIGNATURE' not in headers or
                    headers['X-HUB-SIGNATURE'] != signature):
                return None

        if headers['X-Gitlab-Event'] != 'Push Hook':
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
        return self.gitlab_login_url + "/login/oauth/authorize?scope=read:org&client_id=" + \
            self.oauth_key


    def getAccessTokenFromAuthCallbackCode(self, code):
        response = requests.post(
            self.gitlab_login_url + '/login/oauth/access_token',
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
            self.gitlab_api_url + "/applications/%s/tokens/%s" % (
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
            self.gitlab_api_url + "/orgs/%s/members/%s?access_token=%s" % (
                self.owner,
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
        """Given a gitlab access token, find out what user the token is assigned to."""
        if self.auth_disabled:
            return "user"

        return simplejson.loads(
            requests.get(self.gitlab_api_url + "/user?access_token=" + access_token, verify=self.shouldVerify()).text
            )['login']


    def commit_url(self, commit_id):
        repoName, commitHash = commit_id.split("/")
        return self.gitlab_url + "/%s/%s/commit/%s" % (self.owner, repoName, commitHash)

    def refresh(self):
        for r in self.repos.values():
            r.refresh()
