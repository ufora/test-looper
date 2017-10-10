import collections
from hashlib import md5
import os
import test_looper.core.SubprocessRunner as SubprocessRunner
import subprocess
import sys
import tempfile
import logging

def call(command, **kwargs):
    if not kwargs.get('stdout'):
        kwargs['stdout'] = sys.stdout
    if not kwargs.get('stderr'):
        kwargs['stderr'] = sys.stderr
    return subprocess.call(command, shell=True, **kwargs)


def call_quiet(command):
    with open(os.devnull, "w") as devnull:
        return call(command, stdout=devnull, stderr=devnull)


def check_output(command):
    return subprocess.check_output(command, shell=True)


def hash_files_in_path(path):
    h = md5()
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(dirs)  # walk directories in lexicographic order
        for file_name in sorted(files):
            with open(os.path.join(root, file_name)) as f:
                h.update(f.read())

    return h.hexdigest()

def hash_string(s):
    h = md5()
    h.update(s)
    return h.hexdigest()

class MissingImageError(Exception):
    def __init__(self, image_id):
        super(MissingImageError, self).__init__()
        self.image_id = image_id

    def __str__(self):
        return "No docker image with id '%s'" % self.image_id


class DockerImage(object):
    @property
    def binary(self):
        return "docker"

    def __init__(self, image_name):
        self.image = image_name

    @classmethod
    def from_dockerfile(cls, dockerfile_dir, docker_repo, create_missing=False):
        if bool(dockerfile_dir) != bool(docker_repo): # logical xor
            raise ValueError("You must specify both 'dockerfile_dir' and 'docker_repo' or neither")

        if not dockerfile_dir:
            return None

        dockerfile_dir_hash = hash_files_in_path(dockerfile_dir)

        docker_image = "{docker_repo}:{hash}".format(docker_repo=docker_repo,
                                                     hash=dockerfile_dir_hash)

        docker = DockerImage(docker_image)
        has_image = docker.pull()
        if not has_image:
            if create_missing:
                docker.build(dockerfile_dir)
                docker.push()
            else:
                raise MissingImageError(docker_image)

        return docker

    def subprocessCommandsToRun(self, command, directories, build_env):
        volumes = []
        env_vars = []

        for path in directories:
            path = os.path.abspath(path)

            volumes += ["-v", "%s:%s" % (path,path)]

        for var, val in build_env.items():
            env_vars += ["--env", "%s=%s" % (var,val)]

        return [
            self.binary,
            "run",
            "--rm"] +  volumes + env_vars + [
            "-w", os.getcwd(),
            "--label", "test_looper_worker",
            self.image,
            "/bin/bash",
            "-c",
            command
            ]


    @classmethod
    def from_dockerfile_as_string(cls, docker_repo, dockerfile_as_string, create_missing=False):
        dockerfile_hash = hash_string(dockerfile_as_string)

        if docker_repo is not None:
            docker_image = "{docker_repo}/test_looper:{hash}".format(docker_repo=docker_repo, hash=dockerfile_hash)
        else:
            docker_image = "test_looper:{hash}".format(hash=dockerfile_hash)

        docker = DockerImage(docker_image)

        if not docker.image_exists():
            if create_missing:
                logging.info("Building docker image %s from source...", docker_image)

                docker.buildFromString(dockerfile_as_string)
                if docker_repo is not None:
                    docker.push()
            else:
                raise MissingImageError(docker_image)

        return docker


    def image_exists(self):
        return call_quiet("{docker} image inspect {image}".format(docker=self.binary,
                                                   image=self.image)) == 0

    def pull(self):
        return call("{docker} pull {image}".format(docker=self.binary,
                                                   image=self.image)) == 0

    
    def disable_build_cache(self):
        return False

    def build(self, dockerfile_dir):
        subprocess.check_call(
            "{docker} build --label test_looper_worker {cache_builds} -t {image} {path}"
                .format(docker=self.binary,
                        image=self.image,
                        path=dockerfile_dir,
                        cache_builds="--no-cache" if self.disable_build_cache() else ""
                        ),
            shell=True,
            stdout=sys.stdout,
            stderr=sys.stderr
            )

    def buildFromString(self, dockerfile_text,timeout=None):
        with tempfile.NamedTemporaryFile() as tmp:
            print >> tmp, dockerfile_text
            tmp.flush()

            output = []

            def onStdOut(m):
                output.append(m)
                print m

            proc = SubprocessRunner.SubprocessRunner(
                ["{docker} build --label test_looper_worker {cache_builds} -t {image} - < {tmpfile}"
                    .format(
                        docker=self.binary,
                        image=self.image,
                        tmpfile=tmp.name,
                        cache_builds="--no-cache" if self.disable_build_cache() else ""
                        )],
                onStdOut,
                onStdOut,
                shell=True,
                )

            proc.start()
            result = proc.wait(timeout=timeout)

            if result != 0:
                raise Exception("Failed to build dockerfile:\n%s" % ("\n".join(output)))

    def push(self):
        assert self.docker_repo is not None

        subprocess.check_call(
            "{docker} push {image}"
                .format(docker=self.binary, image=self.image),
            shell=True,
            stdout=sys.stdout,
            stderr=sys.stderr
            )


    def run(self,
            command='',
            name=None,
            volumes=None,
            env=None,
            options=None,
            stdout=None,
            stderr=None):
        def caller(command):
            return call(command, stdout=stdout, stderr=stderr)

        return self._run(caller, command, name, volumes, env, options)


    def call(self, command, volumes=None, env=None, options=None):
        options = options or ''
        options += ' --rm'

        return self._run(check_output,
                         command,
                         name=None,
                         volumes=volumes,
                         env=env,
                         options=options)


    def _run(self, call_func, command, name=None, volumes=None, env=None, options=None):
        name = name or ''
        if name:
            name = '--name=' + name

        if env:
            env = ' '.join('--env {0}={1}'.format(k, v) for k, v in env.iteritems())

        if isinstance(volumes, collections.Mapping):
            volumes = ' '.join('--volume %s:%s' % (k, volumes[k]) for k in volumes)

        return call_func(
            "{docker} run {options} {name} {volumes} {env} {image} {command}".format(
                docker=self.binary,
                options=options or '',
                name=name,
                volumes=volumes or '',
                env=env or '',
                image=self.image,
                command=command)
            )


    def stop(self, container_name):
        return call_quiet("{docker} stop {name} > /dev/null".format(docker=self.binary,
                                                                    name=container_name))


    def remove(self, container_name):
        return call_quiet("{docker} rm {name} > /dev/null".format(docker=self.binary,
                                                                  name=container_name))
