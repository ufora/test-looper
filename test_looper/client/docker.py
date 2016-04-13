import collections
from hashlib import md5
import os
import subprocess
import sys

def call(command, quiet=False):
    kwargs = {} if quiet else {'stdout': sys.stdout, 'stderr': sys.stderr}
    return subprocess.call(command, shell=True, **kwargs)


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


class MissingImageError(Exception):
    def __init__(self, image_id):
        super(MissingImageError, self).__init__()
        self.image_id = image_id

    def __str__(self):
        return "No docker image with id '%s'" % self.image_id


class Docker(object):
    binary = None

    def __init__(self, image_name):
        self.image = image_name


    @property
    def docker_binary(self):
        if self.binary is None:
            self.binary = "nvidia-docker" if self.is_gpu() else "docker"
        return self.binary


    @staticmethod
    def is_gpu():
        return call('nvidia-smi', quiet=True) == 0 and \
               call('which nvidia-docker', quiet=True) == 0

    @classmethod
    def from_dockerfile(cls, dockerfile_dir, docker_repo, create_missing=False):
        if bool(dockerfile_dir) != bool(docker_repo): # logical xor
            raise ValueError("You must specify both 'dockerfile_dir' and 'docker_repo' or neither")

        if not dockerfile_dir:
            return None


        dockerfile_dir_hash = hash_files_in_path(dockerfile_dir)
        docker_image = "{docker_repo}:{hash}".format(docker_repo=docker_repo,
                                                     hash=dockerfile_dir_hash)

        docker = Docker(docker_image)
        has_image = docker.pull()
        if not has_image:
            if create_missing:
                docker.build(dockerfile_dir)
                docker.push()
            else:
                raise MissingImageError(docker_image)

        return docker


    def pull(self):
        return call("{docker} pull {image}".format(docker=self.docker_binary,
                                                   image=self.image)) == 0


    def build(self, dockerfile_dir):
        subprocess.check_call("{docker} build -t {image} {path}".format(docker=self.docker_binary,
                                                                        image=self.image,
                                                                        path=dockerfile_dir),
                              shell=True,
                              stdout=sys.stdout,
                              stderr=sys.stderr)

    def push(self):
        subprocess.check_call("{docker} push {image}".format(docker=self.docker_binary,
                                                             image=self.image),
                              shell=True,
                              stdout=sys.stdout,
                              stderr=sys.stderr)


    def run(self, command='', name=None, volumes=None, env=None, options=None):
        return self._run(call, command, name, volumes, env, options)


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
                docker=self.docker_binary,
                options=options or '',
                name=name,
                volumes=volumes or '',
                env=env or '',
                image=self.image,
                command=command)
            )


    def stop(self, container_name):
        return call("{docker} stop {name} > /dev/null".format(docker=self.docker_binary,
                                                              name=container_name),
                    quiet=True)


    def remove(self, container_name):
        return call("{docker} rm {name} > /dev/null".format(docker=self.docker_binary,
                                                            name=container_name),
                    quiet=True)
