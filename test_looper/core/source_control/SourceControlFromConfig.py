import test_looper.core.source_control.LocalGitRepo as LocalGitRepo
import test_looper.core.source_control.Bitbucket as Bitbucket
import test_looper.core.source_control.Github as Github
import os

TEST_LOOPER_GITHUB_ACCESS_TOKEN = "TEST_LOOPER_GITHUB_ACCESS_TOKEN"
TEST_LOOPER_OAUTH_KEY = "TEST_LOOPER_OAUTH_KEY"
TEST_LOOPER_OAUTH_SECRET = "TEST_LOOPER_OAUTH_SECRET"

def configureGithub(src_ctrl_config):
    oauth_key = src_ctrl_config['oauth_key']
    oauth_secret = src_ctrl_config['oauth_secret']
    github_access_token = src_ctrl_config['access_token']

    src_ctrl_args = {
        'path_to_local_repo': str(os.path.expandvars(src_ctrl_config["path_to_local_repo"])),
        'oauth_key': oauth_key,
        'oauth_secret': oauth_secret,
        'webhook_secret': str(src_ctrl_config['webhook_secret']),
        'owner': src_ctrl_config['target_repo_owner'],
        'repo': src_ctrl_config['target_repo'],
        'test_definitions_path': src_ctrl_config['test_definitions_path'],
        'access_token': github_access_token,
        'auth_disabled': src_ctrl_config.get("auth_disabled", False),
        'clone_url': src_ctrl_config["clone_url"]
        }

    for item in ['github_url', 'github_login_url', 'github_api_url']:
        if item in src_ctrl_config:
            src_ctrl_args[item] = src_ctrl_config[item]

    return Github.Github(**src_ctrl_args)

def configureBitbucket(src_ctrl_config):
    oauth_key = src_ctrl_config.get('oauth_key') or os.getenv(TEST_LOOPER_OAUTH_KEY)
    if oauth_key is None and config["auth"] == "none" != 'none':
        logging.critical("Either 'oauth.key' config setting or %s must be set.",
                         TEST_LOOPER_OAUTH_KEY)

    oauth_secret = src_ctrl_config.get('oauth_secret') or os.getenv(TEST_LOOPER_OAUTH_SECRET)
    if oauth_secret is None and config["auth"] == "none" != 'none':
        logging.critical("Either 'oauth.secret' config setting or %s must be set.",
                         TEST_LOOPER_OAUTH_SECRET)

    access_token = src_ctrl_config.get('access_token')
    if github_access_token is None and config["auth"] == "none" != 'none':
        logging.critical("'bitbucket.access_token' config setting must be set.")

    src_ctrl_args = {
        'oauth_key': oauth_key,
        'oauth_secret': oauth_secret,
        'webhook_secret': str(src_ctrl_config.get('webhook_secret')),
        'owner': src_ctrl_config['target_repo_owner'],
        'repo': src_ctrl_config['target_repo'],
        'test_definitions_path': src_ctrl_config['test_definitions_path'],
        'access_token': access_token
        }

    return Bitbucket.Bitbucket(**src_ctrl_args)

def configureGit(config):
    return LocalGitRepo.LocalGitRepo(
        path_or_repo=os.path.expandvars(config['path_to_repo']),
        test_definitions_path=config['test_definitions_path']
        )

def getFromConfig(config):
    if config['type'] == "local_git":
        return configureGit(config)
    if config['type'] == "github":
        return configureGithub(config)
    if config['type'] == "bitbucket":
        return configureBitbucket(config)
    else:
        raise Exception("unknown source control type: %s" % config['type'])
    