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
import os
import subprocess
import sys
import uuid

import test_looper.client.env as env
from test_looper.client.docker import Docker


def build(build_command,
          working_dir=None,
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

    if working_dir:
        build_command = "pushd {working_dir}; {build_command}; popd".format(
            working_dir=working_dir,
            build_command=build_command
            )

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

    docker = Docker.from_dockerfile(dockerfile_dir, docker_repo, create_missing=True)
    if docker:
        # make sure that the dockerfile directory is included in the package
        copy_command = "{copy} && rsync -amR {dockerfile_dir} /tmp/{package}".format(
            copy=copy_command,
            dockerfile_dir=dockerfile_dir,
            package=package_name)
        run_command_in_docker(docker,
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


def test(test_command=None,
         dockerfile_dir=None,
         docker_repo=None,
         docker_links=None,
         docker_env=None):
    # source directory on the host file system
    if test_command is None:
        argv = list(sys.argv[1:])
        if argv and argv[0].endswith('.py'):
            argv = ['python'] + argv
        test_command = ' '.join(argv)

    docker = Docker.from_dockerfile(dockerfile_dir, docker_repo)
    if docker:
        docker_links = docker_links or []
        run_command_in_docker(docker,
                              test_command,
                              os.getcwd(),
                              docker_env=docker_env,
                              options=['--link=%s' % l for l in docker_links])
    else:
        subprocess.check_call(test_command,
                              shell=True,
                              stdout=sys.stdout,
                              stderr=sys.stderr)


def make_build_command(build_command, copy_command, package_command):
    return '{build} && {copy} && {package}'.format(build=build_command,
                                                   copy=copy_command,
                                                   package=package_command)


def run_command_in_docker(docker, command, src_dir, docker_env=None, options=None):
    volumes = get_docker_volumes(src_dir)
    docker_env = dict(docker_env) if docker_env else {}
    docker_env.update(get_docker_environment())
    name = uuid.uuid4().hex

    options = options or []
    assert isinstance(options, list)
    options = options + ['--rm', '--ulimit="core=-1"', '--privileged=true']
    if ',' in docker_env['TEST_LOOPER_MULTIBOX_IP_LIST']:
        # this means we're in a mult-box setting
        options.append('--net=host')

    command = 'bash -c "cd {src_dir}; {command}"'.format(
        src_dir=env.docker_src_dir,
        command=command
        )
    sys.stdout.write("Running command: %s\n" % command)
    try:
        return_code = docker.run(command,
                                 name,
                                 volumes,
                                 docker_env,
                                 ' '.join(options))
        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, command)
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
