import test_looper.core.source_control.Github as Github
import test_looper.core.source_control.Gitlab as Gitlab
import test_looper.core.source_control.ReposOnDisk as ReposOnDisk
import os

def getFromConfig(path_to_local_repo_cache, config):
    if config.matches.Gitlab:
        return Gitlab.Gitlab(path_to_local_repo_cache, config)
    if config.matches.Github:
        return Github.Github(path_to_local_repo_cache, config)
    if config.matches.Local:
        return ReposOnDisk.ReposOnDisk(path_to_local_repo_cache, config)
