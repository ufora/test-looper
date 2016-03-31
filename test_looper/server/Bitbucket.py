import requests
import simplejson

from test_looper.server.Git import Git


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

        return simplejson.loads(response.text)['access_token']
    ## OAuth
    ###########


    def commit_url(self, commit_id):
        return "https://bitbucket.org/%s/%s/commit/%s" % (self.owner, self.repo, commit_id)

