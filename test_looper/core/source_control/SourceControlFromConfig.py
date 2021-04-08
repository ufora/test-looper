import test_looper.core.source_control.Github as Github
import test_looper.core.source_control.Gitlab as Gitlab
import test_looper.core.source_control.ReposOnDisk as ReposOnDisk
import test_looper.core.source_control.Multiple as Multiple
import os
import os.path


def getFromConfig(path_to_local_repo_cache, config):
    if config.matches.Gitlab:
        return Gitlab.Gitlab(path_to_local_repo_cache, config)
    if config.matches.Github:
        return Github.Github(path_to_local_repo_cache, config)
    if config.matches.Local:
        return ReposOnDisk.ReposOnDisk(path_to_local_repo_cache, config)
    if config.matches.Multiple:
        return Multiple.Multiple(
            {
                serverName: getFromConfig(
                    os.path.join(path_to_local_repo_cache, serverName), serverConfig
                )
                for serverName, serverConfig in config.sources.items()
            }
        )
