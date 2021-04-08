import base64
import hashlib
import hmac
import logging
import requests
import urllib
import json
import traceback
import threading
import os

from test_looper.core.source_control import SourceControl
from test_looper.core.tools.Git import Git
import test_looper.core.source_control.RemoteRepo as RemoteRepo


def loadPages(pageFun):
    """Call a function 'pageFun' with increasing integers until it stops returning anything."""
    res = []

    page = 1
    while True:
        page_results = pageFun(page)
        if page_results:
            res.extend(page_results)
            page += 1
        else:
            break

    return res


class Gitlab(SourceControl.SourceControl):
    def __init__(self, path_to_local_repo_cache, config):
        super(Gitlab, self).__init__()

        self.auth_disabled = config.auth_disabled

        self.path_to_local_repo_cache = path_to_local_repo_cache

        self.oauth_key = config.oauth_key
        self.oauth_secret = config.oauth_secret
        self.private_token = config.private_token
        self.webhook_secret = config.webhook_secret
        self.owner = config.group
        self.gitlab_url = config.gitlab_url
        self.gitlab_api_url = config.gitlab_api_url
        self.gitlab_login_url = config.gitlab_login_url
        self.gitlab_clone_url = config.gitlab_clone_url

        self.lock = threading.Lock()
        self.repos = {}

    def shouldVerify(self):
        return True

    def isWebhookInstalled(self, reponame, server_port_config):
        if server_port_config is None:
            return False

        def get_hooks(page):
            url = self.gitlab_api_url + (
                "/projects/%s/hooks?" % (urllib.parse.quote(reponame, safe=""))
                + urllib.parse.urlencode(
                    {
                        "private_token": self.private_token,
                        "per_page": "20",
                        "page": str(page),
                    }
                )
            )

            headers = {"accept": "application/json"}

            response = json.loads(
                requests.get(url, headers=headers, verify=self.shouldVerify()).content
            )

            if "message" in response:
                return []

            return response

        hooks = loadPages(get_hooks)

        url = "https://%s%s/" % (
            server_port_config.server_address,
            ":" + str(server_port_config.server_https_port)
            if server_port_config.server_https_port != 443
            else "",
        )

        for h in hooks:
            if h["push_events"] and h["url"] in [
                url + "webhook",
                url + "githubReceivedAPush",
            ]:
                return True

        return False

    def installWebhook(self, reponame, server_port_config):
        url = "https://%s%s/webhook" % (
            server_port_config.server_address,
            ":" + str(server_port_config.server_https_port)
            if server_port_config.server_https_port != 443
            else "",
        )

        target = self.gitlab_api_url + "/projects/%s/hooks" % urllib.parse.quote(
            reponame, safe=""
        )

        response = requests.post(
            target,
            headers={"accept": "application/json", "PRIVATE-TOKEN": self.private_token},
            data={"url": url, "push_events": True},
            verify=self.shouldVerify(),
        )

        if response.status_code != 201:
            logging.error(
                "Response to request to create webhook: %s with "
                "contents %s\n\ntgt=%s\nurl=%s",
                response,
                response.content,
                target,
                url,
            )
            return False
        else:
            logging.info("Sucessfully installed a webhook into %s", reponame)

        return True

    def listReposAtPage(self, page):
        res = []

        url = (
            self.gitlab_api_url
            + "/projects?"
            + urllib.parse.urlencode(
                {
                    "private_token": self.private_token,
                    "per_page": "20",
                    "page": str(page),
                }
            )
        )

        headers = {"accept": "application/json"}

        response = requests.get(url, headers=headers, verify=self.shouldVerify())

        try:
            jsonContents = json.loads(response.content)
            if "message" in jsonContents:
                logging.error("Got an error response: %s", response.content)
            else:
                for r in jsonContents:
                    try:
                        res.append(r["namespace"]["full_path"] + "/" + r["name"])
                    except:
                        logging.error("failed with: %s", jsonContents)
                        logging.error(traceback.format_exc())
        except:
            logging.error(traceback.format_exc())

        logging.info("Repo %s had %s", self.gitlab_api_url, res)

        return res

    def listRepos(self):
        res = loadPages(self.listReposAtPage)

        return [x for x in res if x.lower().startswith(self.owner.lower())]

    def getRepo(self, repoName):
        with self.lock:
            if repoName not in self.repos:
                path = os.path.join(self.path_to_local_repo_cache, repoName)
                self.repos[repoName] = RemoteRepo.RemoteRepo(repoName, path, self)
                logging.info("Initializing repo %s at %s", repoName, path)

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

        if headers["X-Gitlab-Event"] != "Push Hook":
            return None

        if self.gitlab_url not in payload["project"]["web_url"]:
            return None

        return {
            "branch": payload["ref"].split("/")[-1],
            "repo": payload["project"]["path_with_namespace"],
        }

    ###########
    ## OAuth
    def authenticationUrl(self):
        if self.auth_disabled:
            return None

        """Return the url to which we should direct unauthorized users"""
        return (
            self.gitlab_login_url
            + "/login/oauth/authorize?scope=read:org&client_id="
            + self.oauth_key
        )

    def getAccessTokenFromAuthCallbackCode(self, code):
        response = requests.post(
            self.gitlab_login_url + "/login/oauth/access_token",
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
            self.gitlab_api_url
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
            self.gitlab_api_url
            + "/orgs/%s/members/%s?access_token=%s"
            % (self.owner, user["user"]["login"], access_token),
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
        """Given a gitlab access token, find out what user the token is assigned to."""
        if self.auth_disabled:
            return "user"

        return json.loads(
            requests.get(
                self.gitlab_api_url + "/user?access_token=" + access_token,
                verify=self.shouldVerify(),
            ).text
        )["login"]

    def cloneUrl(self, repoName):
        return self.gitlab_clone_url + ":" + repoName + ".git"

    def commit_url(self, repoName, commitHash):
        assert repoName is not None
        return self.gitlab_url + "/%s/commit/%s" % (repoName, commitHash)

    def refresh(self):
        for r in self.repos.values():
            r.refresh()
