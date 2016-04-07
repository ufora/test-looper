import base64
import hashlib
import hmac
import logging
import requests
import simplejson

from test_looper.core.TestScriptDefinition import TestScriptDefinition
from test_looper.server.Git import Git


class Github(Git):
    def __init__(self,
                 oauth_key,
                 oauth_secret,
                 access_token,
                 webhook_secret,
                 owner,
                 repo,
                 test_definitions_path):
        assert access_token is not None

        super(Github, self).__init__()

        self.oauth_key = oauth_key
        self.oauth_secret = oauth_secret
        self.access_token = access_token
        self.webhook_secret = webhook_secret
        self.owner = owner
        self.repo = repo
        self.test_definitions_path = test_definitions_path


    ###########
    ## OAuth
    def authenticationUrl(self):
        """Return the url to which we should direct unauthorized users"""
        return "https://github.com/login/oauth/authorize?scope=read:org&client_id=" + \
            self.oauth_key


    def getAccessTokenFromAuthCallbackCode(self, code):
        response = requests.post(
            'https://github.com/login/oauth/access_token',
            headers={
                'accept': 'application/json'
                },
            data={
                'client_id': self.oauth_key,
                'client_secret': self.oauth_secret,
                'code': code
                }
            )

        return simplejson.loads(response.text)['access_token']
    ## OAuth
    ###########

    def verify_webhook_request(self, headers, body):
        signature = "sha1=" + hmac.new(self.webhook_secret, body, hashlib.sha1).hexdigest()
        if 'X-HUB-SIGNATURE' not in headers or headers['X-HUB-SIGNATURE'] != signature:
            return None

        payload = simplejson.loads(body)
        return {
            'branch': payload['ref'].split('/')[-1],
            'repo': payload['repository']['name']
            }




    def authorize_access_token(self, access_token):
        logging.info("Checking access token %s", access_token)

        response = requests.get(
            "https://api.github.com/applications/%s/tokens/%s" % (
                self.oauth_key, access_token
                ),
            auth=requests.auth.HTTPBasicAuth(self.oauth_key,
                                             self.oauth_secret)
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
            "https://api.github.com/orgs/%s/members/%s?access_token=%s" % (
                self.owner,
                user['user']['login'],
                access_token
                )
            )
        if response.status_code == 204:
            return True

        logging.info(
            "Denying access for user %s because they are not a member of the %s owner",
            user['user']['login'],
            self.owner
            )
        return False


    @staticmethod
    def getUserNameFromToken(access_token):
        """Given a github access token, find out what user the token is assigned to."""
        return simplejson.loads(
            requests.get("https://api.github.com/user?access_token=" + access_token).text
            )['login']


    def commit_url(self, commit_id):
        return "https://github.com/%s/%s/commit/%s" % (self.owner, self.repo, commit_id)


    def getServerAuthParameters(self):
        """Return authorization parameters for GitHub API request using the server
        credentials"""
        return "access_token=" + self.access_token

    def getTestScriptDefinitionsForCommit(self, commitId):
        responseTestDefinitions = requests.get(
            "https://api.github.com/repos/%s/%s/contents/%s?ref=%s&%s" % (
                self.owner,
                self.repo,
                self.test_definitions_path,
                commitId,
                self.getServerAuthParameters()
                )
            )


        testDefinitionsJson = simplejson.loads(responseTestDefinitions.text)

        if 'message' in testDefinitionsJson and testDefinitionsJson['message'] == "Not Found":
            return []

        try:
            results = simplejson.loads(base64.b64decode(testDefinitionsJson['content']))
            return TestScriptDefinition.bulk_load(results)
        except:
            logging.warn(
                "Contents of %s for %s are invalid",
                self.test_definitions_path,
                commitId
                )
            return []

