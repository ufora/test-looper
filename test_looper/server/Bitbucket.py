import logging
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

        logging.info("access_token response: %s", response.text)
        return simplejson.loads(response.text)['access_token']


    def authorize_access_token(self, access_token):
        logging.info("Checking access token %s", access_token)

        response = requests.get(
            "https://api.bitbucket.org/2.0/repositories/%s/%s" % (self.owner, self.repo),
            headers={'Authorization': 'Bearer %s' % access_token}
            )
        if not response.ok:
            logging.info(
                "Denying access for token %s because we can't access the repo: %s",
                access_token,
                response.text
                )
            return False


        logging.info("Repo GET response: %s", response.text)
        return True

        #user = simplejson.loads(response.text)
        #if not 'user' in user or not 'login' in user['user']:
            #logging.info(
                #"Denying access for token %s because auth response didn't include user info",
                #access_token
                #)
            #return False

        #response = requests.get(
            #"https://api.github.com/orgs/%s/members/%s?access_token=%s" % (
                #self.owner,
                #user['user']['login'],
                #access_token
                #)
            #)
        #if response.status_code == 204:
            #return True

        #logging.info(
            #"Denying access for user %s because they are not a member of the %s owner",
            #user['user']['login'],
            #self.owner
            #)
        #return False
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
        return simplejson.loads(response.text)['display_name']


    def commit_url(self, commit_id):
        return "https://bitbucket.org/%s/%s/commit/%s" % (self.owner, self.repo, commit_id)

