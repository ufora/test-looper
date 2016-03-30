import base64
import cPickle as pickle
import logging
import requests
import simplejson
import subprocess
import traceback

import test_looper.core.OutOfProcessDownloader as OutOfProcessDownloader
from test_looper.core.TestScriptDefinition import TestScriptDefinition

class SubprocessCheckCall(object):
    def __init__(self, args, kwds):
        self.args = args
        self.kwds = kwds

    def __call__(self):
        return pickle.dumps(subprocess.check_call(*self.args, **self.kwds))

class SubprocessCheckOutput(object):
    def __init__(self, args, kwds):
        self.args = args
        self.kwds = kwds

    def __call__(self):
        return pickle.dumps(subprocess.check_output(*self.args, **self.kwds))


class Github(object):
    def __init__(self,
                 appClientId,
                 appClientSecret,
                 githubAccessToken,
                 organization,
                 repo,
                 testDefinitionsPath):
        #assert appClientId is not None
        #assert appClientSecret is not None
        assert githubAccessToken is not None

        self.appClientId = appClientId
        self.appClientSecret = appClientSecret
        self.githubAccessToken = githubAccessToken
        self.organization = organization
        self.repo = repo
        self.testDefinitionsPath = testDefinitionsPath

        self.outOfProcessDownloaderPool = \
            OutOfProcessDownloader.OutOfProcessDownloaderPool(1, dontImportSetup=True)

    def subprocessCheckCall(self, *args, **kwds):
        return pickle.loads(
            self.outOfProcessDownloaderPool.executeAndReturnResultAsString(
                SubprocessCheckCall(args, kwds)
                )
            )

    def subprocessCheckOutput(self, *args, **kwds):
        return pickle.loads(
            self.outOfProcessDownloaderPool.executeAndReturnResultAsString(
                SubprocessCheckOutput(args, kwds)
                )
            )


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
                self.appClientId, access_token
                ),
            auth=requests.auth.HTTPBasicAuth(self.appClientId,
                                             self.appClientSecret)
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
            self.appClientId

    def getAccessTokenFromAuthCallbackCode(self, code):
        response = requests.post(
            'https://github.com/login/oauth/access_token',
            headers={
                'accept': 'application/json'
                },
            data={
                'client_id': self.appClientId,
                'client_secret': self.appClientSecret,
                'code': code
                }
            )

        return simplejson.loads(response.text)['access_token']

    @staticmethod
    def isValidBranchName_(name):
        return name and '/HEAD' not in name

    def listBranches(self, prefix='origin'):
        self.fetchOrigin()
        if self.subprocessCheckCall('git remote prune origin', shell=True) != 0:
            logging.error("Failed to 'git remote prune origin'. " +
                          "Deleted remote branches may continue to be tested.")
        output = self.subprocessCheckOutput('git branch -r', shell=True).strip().split('\n')
        output = [l.strip() for l in output if l]
        return [l for l in output if l.startswith(prefix) and self.isValidBranchName_(l)]


    def commitIdsParentHashesAndSubjectsInRevlist(self, commitRange):
        """
        Returns the list of commits in the specified range.

        'commitRange' should be a revlist, e.g.

            origin/master ^origin/master^^^^^^

        """
        revisionListToUse = []

        revisions = self.commitsInRevList(commitRange)

        for (commitHash, parentHash, commitTitle) in revisions:
            revisionListToUse.append(
                (commitHash, parentHash, commitTitle)
                )

        return revisionListToUse


    def fetchOrigin(self):
        if self.subprocessCheckCall('git fetch', shell=True) != 0:
            logging.error("Failed to fetch from origin!")

    def commitsInRevList(self, commitRange):
        """Given a revision list, return a list of commits that match.

        Resulting objects are tuples of

            (hash, parent_hash, title, branchName)


        """
        if not commitRange:
            return []

        command = 'git --no-pager log --topo-order ' + \
                commitRange + ' --format=format:"%H %P -- %s"'
        try:
            lines = self.subprocessCheckOutput(command, shell=True).strip().split('\n')
        except subprocess.CalledProcessError:
            stack = ''.join(traceback.format_stack())
            logging.error("error fetching revlist %s\n%s", commitRange, stack)
            raise ValueError("error fetching '%s'" % commitRange)


        lines = [l.strip() for l in lines if l]

        def parseCommitLine(line):
            splitLine = line.split(' ')
            doubleDashes = splitLine.index("--")

            if doubleDashes != 2:
                logging.warn("Got a confusing commit line: %s", line)
                return None

            return (
                splitLine[0],                           # commit hash
                splitLine[1],                           # parent commit
                " ".join(splitLine[doubleDashes+1:])    # commit title
                )

        commitTuples = [parseCommitLine(l) for l in lines if self.isValidBranchName_(l)]

        return [c for c in commitTuples if c is not None]

