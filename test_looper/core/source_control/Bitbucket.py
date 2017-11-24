import logging
import requests
import simplejson
import traceback

from test_looper.core.tools.Git import Git
from test_looper.data_model.TestScriptDefinition import TestScriptDefinition


class Bitbucket(Git):
    def __init__(self,
                 oauth_key,
                 oauth_secret,
                 webhook_secret,
                 owner,
                 repo,
                 test_definitions_path):
        super(Bitbucket, self).__init__()

        self.oauth_key = oauth_key
        self.oauth_secret = oauth_secret
        self.webhook_secret = webhook_secret
        self.owner = owner
        self.repo = repo
        self.test_definitions_path = test_definitions_path
        self.server_access_token = None


    ###########
    ## OAuth
    def authenticationUrl(self):
        """Return the url to which we should direct unauthorized users"""
        return ("https://bitbucket.org/site/oauth2/authorize?"
                "client_id=%s&response_type=code") % self.oauth_key


    def getAccessTokenFromAuthCallbackCode(self, code):
        response = requests.post(
            'https://bitbucket.org/site/oauth2/access_token',
            auth=(self.oauth_key, self.oauth_secret),
            data={
                'grant_type': 'authorization_code',
                'code': '%s' % code
                }
            )
        if not response.ok:
            response.raise_for_status()

        json = response.json()
        if 'access_token' in json:
            return json['access_token']

        logging.error("Failed to get token in OAuth callback. Response: %s",
                      response.text)
        return None


    def authorize_access_token(self, access_token):
        response = requests.get(
            "https://api.bitbucket.org/2.0/repositories/%s/%s" % (self.owner, self.repo),
            headers=self.authorization_headers(access_token)
            )
        if not response.ok:
            logging.info(
                "Denying access for token %s because it can't access the repo: %s",
                access_token,
                response.text
                )
            return False

        return True
    ## OAuth
    ###########


    def verify_webhook_request(self, headers, body):
        if ('X-Hook-UUID' not in headers or 'X-Event-Key' not in headers or
                headers['X-Hook-UUID'] != self.webhook_secret):
            logging.error("Failed to verify webhook request:\nHeaders: %s\nBody:%s",
                          headers,
                          body)
            return None

        if headers['X-Event-Key'] != 'repo:push':
            return {}

        payload = simplejson.loads(body)
        change = payload['push']['changes'][0]
        return {
            'branch': change['new']['name'] if 'new' in change and change['new'] is not None \
                else change['old']['name'],
            'repo': payload['repository']['name']
            }


    @staticmethod
    def getUserNameFromToken(access_token):
        """Given a github access token, find out what user the token is assigned to."""
        response = requests.get('https://api.bitbucket.org/2.0/user',
                                headers={'Authorization': 'Bearer %s' % access_token})
        if not response.ok:
            logging.error("Unable to retrieve user information from token: %s",
                          response.text)
            return 'unknown'
        return response.json()['display_name']


    def commit_url(self, commit_id):
        return "https://bitbucket.org/%s/%s/commits/%s" % (self.owner, self.repo, commit_id)


    def getTestScriptDefinitionsForCommit(self, commitId):
        url = ('https://api.bitbucket.org/1.0/repositories/'
               '{owner}/{repo}/raw/{commit}/{path}').format(owner=self.owner,
                                                            repo=self.repo,
                                                            commit=commitId,
                                                            path=self.test_definitions_path)
        while True:
            if self.server_access_token is None:
                self.server_access_token = self.get_server_token()

            response = requests.get(url,
                                    headers=self.authorization_headers(self.server_access_token))
            if response.status_code != requests.codes.unauthorized:
                break
            self.server_access_token = None

        if response.status_code == requests.codes.not_found:
            logging.info("No test definitions for commit: %s. Url: %s", commitId, url)
            return []

        if not response.ok:
            response.raise_for_status()

        return response.json()


    def get_server_token(self):
        response = requests.post('https://bitbucket.org/site/oauth2/access_token',
                                 auth=(self.oauth_key, self.oauth_secret),
                                 data={'grant_type': 'client_credentials'})
        if not response.ok:
            response.raise_for_status()

        return response.json()['access_token']


    @staticmethod
    def authorization_headers(access_token):
        return {'Authorization': 'Bearer %s' % access_token}
