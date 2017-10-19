import base64
import hashlib
import hmac
import logging
import requests
import simplejson

from test_looper.core.TestScriptDefinition import TestScriptDefinition
from test_looper.core.tools.Git import Git

class Github(object):
    def __init__(self,
                 path_to_local_repo,
                 oauth_key,
                 oauth_secret,
                 access_token,
                 webhook_secret,
                 owner,
                 repo,
                 test_definitions_path,
                 github_url = "https://github.com",
                 github_login_url = "https://github.com",
                 github_api_url = "https://api.github.com",
                 auth_disabled = False
                 ):
        assert access_token is not None

        self.auth_disabled = auth_disabled

        self.source_repo = Git(path_to_local_repo)

        self.oauth_key = oauth_key
        self.oauth_secret = oauth_secret
        self.access_token = access_token
        self.webhook_secret = webhook_secret
        self.owner = owner
        self.repo = repo
        self.test_definitions_path = test_definitions_path
        self.github_url = github_url
        self.github_api_url = github_api_url
        self.github_login_url = github_login_url

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


    def cloneUrl(self):
        return self.github_url + "/" + self.owner + "/" + self.repo + ".git"

    def listBranches(self):
        self.source_repo.fetchOrigin()

        return [b[len("origin/"):] for b in self.source_repo.listBranches() if b.startswith("origin/")]

    def commitsBetweenCommitIds(self, c1, c2):
        print "Checking commits between ", c1, c2

        self.source_repo.fetchOrigin()

        return self.source_repo.commitsInRevList(c1 + " ^" + c2)
        
    def commitsBetweenBranches(self, branch, baseline):
        self.source_repo.fetchOrigin()
        
        return self.source_repo.commitsInRevList("origin/%s ^origin/%s" % (branch, baseline))

    def getTestScriptDefinitionsForCommit(self, commitId):
        self.source_repo.fetchOrigin()
        
        return self.source_repo.getFileContents(commitId, self.test_definitions_path)

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
            verify=False
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
            verify=False
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
                self.owner,
                user['user']['login'],
                access_token
                ),
            verify=False
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
            requests.get(self.github_api_url + "/user?access_token=" + access_token, verify=False).text
            )['login']


    def commit_url(self, commit_id):
        return self.github_url + "/%s/%s/commit/%s" % (self.owner, self.repo, commit_id)

