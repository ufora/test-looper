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

import collections
from hashlib import md5
import os
import subprocess
import sys
import uuid

import test_looper.client.env as env


class MissingImageError(Exception):
    def __init__(self, image_id):
        super(MissingImageError, self).__init__()
        self.image_id = image_id

    def __str__(self):
        return "No docker image with id '%s'" % self.image_id


def build(build_command,
          package_pattern=None,
          src_dir=None,
          dockerfile_dir=None,
          docker_repo=None):
    """
    Build a project in test-looper

    """
    package_pattern = package_pattern or ['*.py']
    if isinstance(package_pattern, basestring):
        package_pattern = [package_pattern]
    assert isinstance(package_pattern, collections.Iterable)

    # source directory on the host file system
    raw_src_dir = src_dir or os.path.abspath(os.path.dirname(sys.argv[0]))
    # source directory in docker container (if running in docker)
    src_dir = env.docker_src_dir if dockerfile_dir else raw_src_dir
    output_dir = env.docker_output_dir if dockerfile_dir else env.output_dir

    package_name = "{repo}-{commit}".format(repo=env.repo, commit=env.revision)
    package_command = "tar cfvz {tarball_path}.tar.gz -C /tmp {package}".format(
        tarball_path=os.path.join(output_dir, package_name),
        package=package_name
        )
    copy_command = ("rsync -am --include '*/' {includes} --exclude '*' "
                    "{src_dir} /tmp/{package}").format(
                        includes=' '.join("--include '%s'" % p for p in package_pattern),
                        src_dir=os.path.join(src_dir, '*'),
                        package=package_name
                        )

    docker = get_docker_image(dockerfile_dir, docker_repo)
    if docker:
        # make sure that the dockerfile directory is included in the package
        copy_command = "{copy} && rsync -amR {dockerfile_dir} /tmp/{package}".format(
            copy=copy_command,
            dockerfile_dir=dockerfile_dir,
            package=package_name)
        return run_command_in_docker(docker,
                                     make_build_command(build_command,
                                                        copy_command,
                                                        package_command),
                                     raw_src_dir)
    else:
        subprocess.check_call(make_build_command(build_command,
                                                 copy_command,
                                                 package_command),
                              shell=True,
                              stdout=sys.stdout,
                              stderr=sys.stderr)


def test(test_command, dockerfile_dir=None, docker_repo=None):
    pass


def make_build_command(build_command, copy_command, package_command):
    return '{build} && {copy} && {package}'.format(build=build_command,
                                                   copy=copy_command,
                                                   package=package_command)




def get_docker_image(dockerfile_dir, docker_repo, create_missing=True):
    if bool(dockerfile_dir) != bool(docker_repo): # logical xor
        raise ValueError("You must specify both 'dockerfile_dir' and 'docker_repo' or neither")

    if not dockerfile_dir:
        return None

    docker_binary = "nvidia-docker" if is_gpu() else "docker"
    sys.stdout.write("Docker binary: %s\n" % docker_binary)

    dockerfile_dir_hash = hash_files_in_path(dockerfile_dir)
    docker_image = "{docker_repo}:{hash}".format(docker_repo=docker_repo,
                                                 hash=dockerfile_dir_hash)

    docker = Docker(docker_binary, docker_image)
    has_image = docker.pull()
    if not has_image:
        if create_missing:
            docker.build(dockerfile_dir)
            docker.push()
        else:
            raise MissingImageError(docker_image)

    return docker


def is_gpu():
    return call('nvidia-smi', quiet=True) == 0 and call('which nvidia-docker', quiet=True) == 0


def call(command, quiet=False):
    kwargs = {} if quiet else {'stdout': sys.stdout, 'stderr': sys.stderr}
    return subprocess.call(command, shell=True, **kwargs)


def hash_files_in_path(path):
    h = md5()
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(dirs)  # walk directories in lexicographic order
        for file_name in sorted(files):
            with open(os.path.join(root, file_name)) as f:
                h.update(f.read())

    return h.hexdigest()


def run_command_in_docker(docker, command, src_dir):
    volumes = get_docker_volumes(src_dir)
    docker_env = get_docker_environment()
    name = uuid.uuid4().hex
    options = '--rm --ulimit="core=-1" --privileged=true'
    if docker_env['TEST_LOOPER_MULTIBOX_IP_LIST']:
        options += ' --net=host'

    command = 'bash -c "cd {src_dir}; {command}"'.format(
        src_dir=env.docker_src_dir,
        command=command
        )
    sys.stdout.write("Running command: %s\n" % command)
    try:
        return docker.run(command, name, volumes, docker_env, options)
    finally:
        docker.stop(name)
        docker.remove(name)


def get_docker_volumes(src_dir):
    volumes = {
        "src": "--volume {src_dir}:{docker_src_dir}".format(src_dir=src_dir,
                                                            docker_src_dir=env.docker_src_dir),
        "output": '',
        "ccache": ''
        }

    output_dir = env.output_dir
    if output_dir:
        volumes["output"] = "--volume {output_dir}:{docker_output_dir}".format(
            output_dir=output_dir,
            docker_output_dir=env.docker_output_dir)

    ccache_dir = env.ccache_dir
    if ccache_dir:
        volumes["ccache"] = "--volume {ccache_dir}:/volumes/ccache".format(ccache_dir=ccache_dir)

    return " ".join(volumes.itervalues())


def get_docker_environment():
    return {
        'TEST_OUTPUT_DIR': env.docker_output_dir,
        'AWS_AVAILABILITY_ZONE': env.aws_availability_zone,
        'TEST_LOOPER_TEST_ID': env.test_id,
        'TEST_LOOPER_MULTIBOX_IP_LIST': env.multibox_test_machines,
        'TEST_LOOPER_MULTIBOX_OWN_IP': env.own_ip_address,
        'CORE_DUMP_DIR': os.getenv('CORE_DUMP_DIR', ''),
        'REVISION': env.revision
        }


class Docker(object):
    def __init__(self, docker_binary, image_name):
        self.binary = docker_binary
        self.image = image_name


    def pull(self):
        return call("{docker} pull {image}".format(docker=self.binary, image=self.image)) == 0


    def build(self, dockerfile_dir):
        subprocess.check_call("{docker} build -t {image} {path}".format(docker=self.binary,
                                                                        image=self.image,
                                                                        path=dockerfile_dir),
                              shell=True,
                              stdout=sys.stdout,
                              stderr=sys.stderr)

    def push(self):
        subprocess.check_call("{docker} push {image}".format(docker=self.binary,
                                                             image=self.image),
                              shell=True,
                              stdout=sys.stdout,
                              stderr=sys.stderr)


    def run(self, command, name, volumes=None, env=None, options=None):
        if env:
            env = ' '.join('--env {0}={1}'.format(k, v) for k, v in env.iteritems())

        return call(
            "{docker} run {options} --name={name} {volumes} {env} {image} {command}".format(
                docker=self.binary,
                options=options or '',
                name=name,
                volumes=volumes or '',
                env=env or '',
                image=self.image,
                command=command)
            )


    def stop(self, container_name):
        return call("{docker} stop {name}".format(docker=self.binary, name=container_name),
                    quiet=True)


    def remove(self, container_name):
        return call("{docker} rm {name}".format(docker=self.binary, name=container_name),
                    quiet=True)
