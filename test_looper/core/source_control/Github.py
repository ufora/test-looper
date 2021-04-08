import base64
import hashlib
import hmac
import logging
import requests
import json
import traceback
import threading
import os

from test_looper.core.source_control import SourceControl
from test_looper.core.source_control import RemoteRepo
from test_looper.core.tools.Git import Git


class Github(SourceControl.SourceControl):
    def __init__(self, path_to_local_repo_cache, config):
        super(Github, self).__init__()

        self.auth_disabled = config.auth_disabled

        self.path_to_local_repo_cache = path_to_local_repo_cache

        self.oauth_key = config.oauth_key
        self.oauth_secret = config.oauth_secret
        self.access_token = config.access_token
        self.webhook_secret = config.webhook_secret
        self.ownerType, self.ownerName = config.owner.split(":")
        self.github_url = config.github_url
        self.github_api_url = config.github_api_url
        self.github_login_url = config.github_login_url
        self.github_clone_url = config.github_clone_url

        self.lock = threading.Lock()
        self.repos = {}

    def shouldVerify(self):
        return True

    def listRepos(self):
        url = self.github_api_url + "/%ss/%s/repos" % (self.ownerType, self.ownerName)

        headers = {"accept": "application/json"}

        if self.access_token:
            headers["Authorization"] = "token " + self.access_token

        response = requests.get(url, headers=headers, verify=self.shouldVerify())

        res = []
        try:
            for r in json.loads(response.content):
                res.append(r["name"])
        except:
            logging.error("GOT: %s", response.content)
            logging.error(traceback.format_exc())
            return []

        return res

    def getRepo(self, repoName):
        with self.lock:
            if repoName not in self.repos:
                path = os.path.join(self.path_to_local_repo_cache, repoName)
                self.repos[repoName] = RemoteRepo.RemoteRepo(
                    self.ownerName + "/" + repoName, path, self
                )

            repo = self.repos[repoName]

        repo.ensureInitialized()

        return repo

    def verify_webhook_request(self, headers, payload):
        if not self.auth_disabled:
            signature = (
                "sha1=" + hmac.new(self.webhook_secret, body, hashlib.sha1).hexdigest()
            )
            if (
                "X-GitHub-Event" not in headers
                or "X-HUB-SIGNATURE" not in headers
                or headers["X-HUB-SIGNATURE"] != signature
            ):
                return None

        if headers["X-GitHub-Event"] != "push":
            return {}

        return {
            "branch": payload["ref"].split("/")[-1],
            "repo": payload["repository"]["name"],
        }

    ###########
    ## OAuth
    def authenticationUrl(self):
        if self.auth_disabled:
            return None

        """Return the url to which we should direct unauthorized users"""
        return (
            self.github_login_url
            + "/login/oauth/authorize?scope=read:org&client_id="
            + self.oauth_key
        )

    def getAccessTokenFromAuthCallbackCode(self, code):
        response = requests.post(
            self.github_login_url + "/login/oauth/access_token",
            headers={"accept": "application/json"},
            data={
                "client_id": self.oauth_key,
                "client_secret": self.oauth_secret,
                "code": code,
            },
            verify=self.shouldVerify(),
        )

        result = json.loads(response.text)

        if "access_token" not in result:
            logging.error("didn't find 'access_token' in %s", response.text)

        return result["access_token"]

    ## OAuth
    ###########

    def authorize_access_token(self, access_token):
        if self.auth_disabled:
            return True

        logging.info("Checking access token %s", access_token)

        response = requests.get(
            self.github_api_url
            + "/applications/%s/tokens/%s" % (self.oauth_key, access_token),
            auth=requests.auth.HTTPBasicAuth(self.oauth_key, self.oauth_secret),
            verify=self.shouldVerify(),
        )
        if not response.ok:
            logging.info(
                "Denying access for token %s because we can't get the user name",
                access_token,
            )
            return False

        user = json.loads(response.text)
        if not "user" in user or not "login" in user["user"]:
            logging.info(
                "Denying access for token %s because auth response didn't include user info",
                access_token,
            )
            return False

        response = requests.get(
            self.github_api_url
            + "/orgs/%s/members/%s?access_token=%s"
            % (self.ownerName, user["user"]["login"], access_token),
            verify=self.shouldVerify(),
        )
        if response.status_code == 204:
            return True

        logging.info(
            "Denying access for user %s because they are not a member of the %s owner",
            user["user"]["login"],
            self.owner,
        )
        return False

    def getUserNameFromToken(self, access_token):
        """Given a github access token, find out what user the token is assigned to."""
        if self.auth_disabled:
            return "user"

        return json.loads(
            requests.get(
                self.github_api_url + "/user?access_token=" + access_token,
                verify=self.shouldVerify(),
            ).text
        )["login"]

    def cloneUrl(self, repoName):
        return self.github_clone_url + ":" + repoName + ".git"

    def commit_url(self, repoName, commitHash):
        return self.github_url + "/%s/%s/commit/%s" % (
            self.ownerName,
            repoName,
            commitHash,
        )

    def refresh(self):
        for r in self.repos.values():
            r.refresh()
