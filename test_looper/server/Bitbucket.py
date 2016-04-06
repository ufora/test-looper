import logging
import requests
import traceback

from test_looper.server.Git import Git
from test_looper.core.TestScriptDefinition import TestScriptDefinition


class Bitbucket(Git):
    def __init__(self,
                 oauth_key,
                 oauth_secret,
                 owner,
                 repo,
                 test_definitions_path):
        super(Bitbucket, self).__init__()

        self.oauth_key = oauth_key
        self.oauth_secret = oauth_secret
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
        return requests.post(
            'https://bitbucket.org/site/oauth2/access_token',
            auth=(self.oauth_key, self.oauth_secret),
            data={
                'grant_type': 'authorization_code',
                'code': '%s' % code
                }
            ).json()['access_token']


    def authorize_access_token(self, access_token):
        logging.info("Checking access token %s", access_token)

        response = requests.get(
            "https://api.bitbucket.org/2.0/repositories/%s/%s" % (self.owner, self.repo),
            headers=self.authorization_headers(access_token)
            )
        if not response.ok:
            logging.info(
                "Denying access for token %s because we can't access the repo: %s",
                access_token,
                response.text
                )
            return False

        return True
    ## OAuth
    ###########

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
        return "https://bitbucket.org/%s/%s/commit/%s" % (self.owner, self.repo, commit_id)


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

        if not response.ok:
            response.raise_for_status()

        try:
            return TestScriptDefinition.bulk_load(response.json())
        except:
            logging.warn(
                "Contents of %s for %s are invalid: %s\n%s",
                self.test_definitions_path,
                commitId,
                response.text,
                traceback.format_exc()
                )
            return []


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
