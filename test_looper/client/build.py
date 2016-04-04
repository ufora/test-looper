#   Copyright 2015-2016 Ufora Inc.
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from hashlib import md5
import os
import subprocess


def build(docker_path=None, docker_repo=None):
    """
    Build a project in test-looper

    """
    docker_image = get_docker_image(docker_path, docker_repo)


def get_docker_image(docker_path, docker_repo):
    if bool(docker_path) != bool(docker_repo): # logical xor
        raise ValueError("You must specify both 'docker_path' and 'docker_repo' or neither")

    if not docker_path:
        return None

    docker_binary = "nvidia-docker" if has_nvidia_docker() else "docker"
    docker_path_hash = hash_files_in_path(docker_path)


def has_nvidia_docker():
    return subprocess.call('which nvidia-docker', shell=True) == 0


def hash_files_in_path(path):
    h = md5()
    for root, _, files in os.walk(path):
        for file_name in sorted(files):
            with open(os.path.join(root, file_name)) as f:
                h.update(f.read())

    return h.hexdigest()



