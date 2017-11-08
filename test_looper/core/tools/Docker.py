import collections
from hashlib import md5
import os
import test_looper.core.SubprocessRunner as SubprocessRunner
import sys
import logging
import docker
import tempfile
import test_looper.core.tools.DockerWatcher as DockerWatcher

docker_client = docker.from_env()
docker_client.containers.list()


class DockerContainerCleanup(object):
    def __init__(self):
        self.running_containers = []

    def __enter__(self, *args):
        self.running_container_ids = set([c.id for c in docker_client.containers.list()])

    def __exit__(self, *args):
        all_containers = docker_client.containers.list()

        logging.info("Checking docker containers. We started with %s containers, and have %s now",
            len(self.running_container_ids),
            len(all_containers)
            )

        for c in all_containers:
            if c.id not in self.running_container_ids:
                try:
                    logging.info("Removing left-behind docker container %s", c)
                    c.remove(force=True)
                except:
                    logging.error("Failed to remove docker container %s", c.id)

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

        image = DockerImage(docker_image)
        has_image = image.pull()
        if not has_image:
            if create_missing:
                image.build(dockerfile_dir)
                if docker_repo is not None:
                    image.push()
            else:
                raise MissingImageError(docker_image)

        return image

    def subprocessCommandsToRun(self, command, workingDir, directories, build_env, expose_docker_socket=True, net_host=True):
        options = []

        for path,target in directories.iteritems():
            path = os.path.abspath(path)

            options += ["-v", "%s:%s" % (path,target)]

        if expose_docker_socket:
            options += ["-v", "/var/run/docker.sock:/var/run/docker.sock"]

        for var, val in build_env.items():
            options += ["--env", "%s=%s" % (var,val)]

        if net_host:
            options += ["--net=host"]

        return [
            self.binary,
            "run",
            "--rm"] +  options + [
            "-w", workingDir,
            "--label", "test_looper_worker",
            self.image,
            "/bin/bash",
            "-c",
            command
            ]

    @staticmethod
    def removeDanglingDockerImages():
        for c in docker_client.images.list(filters={'dangling':True,'label':'test_looper_worker'}):
            docker_client.images.remove(c.id)

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
        return SubprocessRunner.callAndReturnResultWithoutOutput(
            "{docker} inspect {image}".format(docker=self.binary,image=self.image),
            shell=True
            ) == 0

    def pull(self):
        return SubprocessRunner.callAndReturnResultWithoutOutput(
            "{docker} pull {image}".format(docker=self.binary, image=self.image),
            shell=True
            ) == 0

    
    def disable_build_cache(self):
        return False

    def build(self, dockerfile_dir):
        SubprocessRunner.callAndReturnResultWithoutOutput(
            "{docker} build --label test_looper_worker {cache_builds} -t {image} {path}"
                .format(docker=self.binary,
                        image=self.image,
                        path=dockerfile_dir,
                        cache_builds="--no-cache" if self.disable_build_cache() else ""
                        ),
            shell=True
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

        SubprocessRunner.callAndReturnResultWithoutOutput(
            "{docker} push {image}"
                .format(docker=self.binary, image=self.image),
            shell=True
            )

    def run(self,
            command='',
            name=None,
            volumes=None,
            env=None,
            options=None,
            stdout=None,
            stderr=None):
        return self._run(command, name, volumes, env, options)


    def call(self, command, volumes=None, env=None, options=None):
        options = options or ''
        options += ' --rm'

        return self._run(command,
                         name=None,
                         volumes=volumes,
                         env=env,
                         options=options)


    def _run(self, command, name=None, volumes=None, env=None, options=None):
        name = name or ''
        if name:
            name = '--name=' + name

        if env:
            env = ' '.join('--env {0}={1}'.format(k, v) for k, v in env.iteritems())

        if isinstance(volumes, collections.Mapping):
            volumes = ' '.join('--volume %s:%s' % (k, volumes[k]) for k in volumes)

        cmd = "{docker} run {options} {name} {volumes} {env} {image} {command}".format(
                docker=self.binary,
                options=options or '',
                name=name,
                volumes=volumes or '',
                env=env or '',
                image=self.image,
                command=command)

        return SubprocessRunner.callAndReturnResultWithoutOutput(
            cmd,
            shell=True
            )

    def subprocessRunnerFor(self, dockerargs, commands, onOut, onErr):
        return SubprocessRunner.SubprocessRunner(
            [self.binary, "run"] + dockerargs + [self.image] + commands,
            onOut, onErr
            )


    def stop(self, container_name):
        return SubprocessRunner.callAndReturnResultWithoutOutput(
            "{docker} stop {name}".format(docker=self.binary, name=container_name),
            shell=True
            )


    def remove(self, container_name):
        return SubprocessRunner.callAndReturnResultWithoutOutput(
            "{docker} rm {name}".format(docker=self.binary, name=container_name),
            shell=True
            )
