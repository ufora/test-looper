import base64
import logging
import requests
import simplejson

from test_looper.core.TestScriptDefinition import TestScriptDefinition
from test_looper.server.Git import Git


class Github(Git):
    def __init__(self,
                 oauth_key,
                 oauth_secret,
                 githubAccessToken,
                 organization,
                 repo,
                 testDefinitionsPath):
        assert githubAccessToken is not None

        super(Github, self).__init__()

        self.oauth_key = oauth_key
        self.oauth_secret = oauth_secret
        self.githubAccessToken = githubAccessToken
        self.organization = organization
        self.repo = repo
        self.testDefinitionsPath = testDefinitionsPath


    def linkToCommit(self, commitId):
        return "https://github.com/%s/%s/commit/%s" % (self.organization, self.repo, commitId)


    def getServerAuthParameters(self):
        """Return authorization parameters for GitHub API request using the server
        credentials"""
        return "access_token=" + self.githubAccessToken

    def getTestScriptDefinitionsForCommit(self, commitId):
        responseTestDefinitions = requests.get(
            "https://api.github.com/repos/%s/%s/contents/%s?ref=%s&%s" % (
                self.organization,
                self.repo,
                self.testDefinitionsPath,
                commitId,
                self.getServerAuthParameters()
                )
            )


        testDefinitionsJson = simplejson.loads(responseTestDefinitions.text)

        if 'message' in testDefinitionsJson and testDefinitionsJson['message'] == "Not Found":
            return []

        definitions = []
        results = None

        try:
            results = simplejson.loads(base64.b64decode(testDefinitionsJson['content']))
        except:
            logging.warn(
                "Contents of testDefinitions.json for %s are not valid json",
                commitId
                )
            return []

        build_definition = None
        if isinstance(results, dict) and 'tests' in results:
            build_definition = results.get('build')
            results = results['tests']

        if isinstance(results, list):
            # old testDefinitions.json format - this path is left for backward
            # compatibility and should be removed at some point
            try:
                definitions = [TestScriptDefinition.fromJson(row) for row in results]
            except:
                logging.warn(
                    "contents of testDefinitions.json for %s contained an invalid row",
                    commitId
                    )
                return []

            if build_definition:
                build_definition['name'] = 'build'
                definitions.append(TestScriptDefinition.fromJson(build_definition))
            elif not [x for x in definitions if x.testName == "build"]:
                definitions.append(
                    TestScriptDefinition('build', '', {'cores': 32})
                    )

            return definitions
        else:
            logging.warn(
                "Contents of testDefinitions.json for %s are not a list of test definitions",
                commitId
                )
        return []

    def checkAccessTokenWithGithubServer(self, access_token):
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
                self.organization,
                user['user']['login'],
                access_token
                )
            )
        if response.status_code == 204:
            return True

        logging.info(
            "Denying access for user %s because they are not a member of the %s organization",
            user['user']['login'],
            self.organization
            )
        return False


    @staticmethod
    def getLoginForAccessToken(access_token):
        """Given a github access token, find out what user the token is assigned to."""
        return simplejson.loads(
            requests.get("https://api.github.com/user?access_token=" + access_token).text
            )['login']

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
