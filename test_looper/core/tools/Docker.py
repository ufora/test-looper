import collections
from hashlib import md5
import os
import test_looper.core.SubprocessRunner as SubprocessRunner
import sys
import logging
import docker
import tempfile
import test_looper.core.tools.DockerWatcher as DockerWatcher
import time

docker_client = docker.from_env()
docker_client.containers.list()


class DockerContainerCleanup(object):
    def __init__(self):
        self.running_containers = []

    def __enter__(self, *args):
        self.running_container_ids = set(
            [c.id for c in docker_client.containers.list()]
        )

    def __exit__(self, *args):
        all_containers = docker_client.containers.list()

        logging.info(
            "Checking docker containers. We started with %s containers, and have %s now",
            len(self.running_container_ids),
            len(all_containers),
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
    h.update(s.encode("utf8"))
    return h.hexdigest()


class MissingImageError(Exception):
    def __init__(self, image_id):
        super(MissingImageError, self).__init__()
        self.image_id = image_id

    def __str__(self):
        return "No docker image with id '%s'" % self.image_id


def killAllWithNamePrefix(name_prefix):
    all_containers = docker_client.containers.list()

    toKill = 0

    for c in all_containers:
        if c.name.startswith(name_prefix):
            toKill += 1
            logging.info("Shutting down container %s with name %s", c, c.name)
            try:
                c.remove(force=True)
            except:
                logging.warn("Failed to kill container %s", c)

    return toKill


class DockerImage(object):
    @property
    def binary(self):
        return "docker"

    def __init__(self, image_name):
        self.image = image_name

    @classmethod
    def from_dockerfile(cls, dockerfile_dir, docker_repo, create_missing=False):
        if bool(dockerfile_dir) != bool(docker_repo):  # logical xor
            raise ValueError(
                "You must specify both 'dockerfile_dir' and 'docker_repo' or neither"
            )

        if not dockerfile_dir:
            return None

        dockerfile_dir_hash = hash_files_in_path(dockerfile_dir)

        docker_image = "{docker_repo}:{hash}".format(
            docker_repo=docker_repo, hash=dockerfile_dir_hash
        )

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

    def subprocessCommandsToRun(
        self,
        command,
        workingDir,
        directories,
        build_env,
        expose_docker_socket=True,
        net_host=True,
    ):
        options = []

        for path, target in directories.items():
            path = os.path.abspath(path)

            options += ["-v", "%s:%s" % (path, target)]

        if expose_docker_socket:
            options += ["-v", "/var/run/docker.sock:/var/run/docker.sock"]

        for var, val in build_env.items():
            options += ["--env", "%s=%s" % (var, val)]

        if net_host:
            options += ["--net=host"]

        return (
            [self.binary, "run", "--rm"]
            + options
            + [
                "-w",
                workingDir,
                "--label",
                "test_looper_worker",
                self.image,
                "/bin/bash",
                "-c",
                command,
            ]
        )

    @staticmethod
    def removeDanglingDockerImages():
        for c in docker_client.images.list(
            filters={"dangling": True, "label": "test_looper_worker"}
        ):
            docker_client.images.remove(c.id)

    @classmethod
    def from_dockerfile_as_string(
        cls,
        docker_repo,
        dockerfile_as_string,
        create_missing=False,
        env_keys_to_passthrough=(),
        logger=None,
    ):
        dockerfile_hash = hash_string(dockerfile_as_string)

        if docker_repo is not None:
            docker_image = "{docker_repo}/test_looper:{hash}".format(
                docker_repo=docker_repo, hash=dockerfile_hash
            )
        else:
            docker_image = "test_looper:{hash}".format(hash=dockerfile_hash)

        docker = DockerImage(docker_image)

        if logger and docker_repo:
            logger("Pulling docker image %s" % docker_image)

            if not docker.pull():
                logger("Pulling image failed.")
            else:
                logger("Pulling image %s succeeded." % docker_image)

        if not docker.image_exists():
            if create_missing:
                if logger:
                    logger("Building docker image %s from source..." % docker_image)

                docker.buildFromString(
                    dockerfile_as_string,
                    env_keys_to_passthrough=env_keys_to_passthrough,
                    logger=logger,
                )

                if docker_repo is not None:
                    logger("pushing docker image")
                    docker.push(logger=logger)
            else:
                raise MissingImageError(docker_image)

        return docker

    def image_exists(self):
        return (
            SubprocessRunner.callAndReturnResultWithoutOutput(
                "{docker} inspect {image}".format(docker=self.binary, image=self.image),
                shell=True,
            )
            == 0
        )

    def pull(self, logger=None, timeout=360, retries=10, sleepAmt=30):
        def onStdOut(msg):
            if logger:
                logger(msg)
            else:
                print(msg)

        tries = 0
        while tries <= retries or retries < 0:
            proc = SubprocessRunner.SubprocessRunner(
                [self.binary, "pull", self.image], onStdOut, onStdOut
            )
            proc.start()

            ret_code = proc.wait(timeout=timeout)
            if ret_code == 0:
                return True
            else:
                time.sleep(sleepAmt)
            tries += 1

        return True if ret_code == 0 else False

    def disable_build_cache(self):
        return False

    def build(self, dockerfile_dir):
        SubprocessRunner.callAndReturnResultWithoutOutput(
            "{docker} build --label test_looper_worker {cache_builds} -t {image} {path}".format(
                docker=self.binary,
                image=self.image,
                path=dockerfile_dir,
                cache_builds="--no-cache" if self.disable_build_cache() else "",
            ),
            shell=True,
        )

    def buildFromString(
        self, dockerfile_text, timeout=None, env_keys_to_passthrough=(), logger=None
    ):
        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(dockerfile_text.encode("ascii") + b"\n")
            tmp.flush()

            output = []

            def onStdOut(m):
                output.append(m)
                if logger:
                    logger(m)
                else:
                    print(m)

            buildargs = []
            for e in env_keys_to_passthrough:
                if os.getenv(e):
                    buildargs.append('--build-arg %s="%s"' % (e, os.getenv(e)))

            args = [
                "{docker} build --label test_looper_worker {cache_builds} {buildargs} -t {image} - < {tmpfile}".format(
                    docker=self.binary,
                    image=self.image,
                    tmpfile=tmp.name,
                    buildargs=" ".join(buildargs),
                    cache_builds="--no-cache" if self.disable_build_cache() else "",
                )
            ]

            proc = SubprocessRunner.SubprocessRunner(
                args, onStdOut, onStdOut, shell=True
            )

            proc.start()
            result = proc.wait(timeout=timeout)

            if result != 0:
                if logger:
                    raise Exception("Failed to build dockerfile")
                else:
                    raise Exception(
                        "Failed to build dockerfile:\n%s" % ("\n".join(output))
                    )

    def push(self, logger=None):
        def output(msg):
            if logger:
                logger(msg)

        runner = SubprocessRunner.SubprocessRunner(
            [self.binary, "push", self.image], output, output
        )
        runner.start()
        result = runner.wait(timeout=360)

        return result != 0

    def run(
        self,
        command="",
        name=None,
        volumes=None,
        env=None,
        options=None,
        stdout=None,
        stderr=None,
    ):
        return self._run(command, name, volumes, env, options)

    def call(self, command, volumes=None, env=None, options=None):
        options = options or ""
        options += " --rm"

        return self._run(command, name=None, volumes=volumes, env=env, options=options)

    def _run(self, command, name=None, volumes=None, env=None, options=None):
        name = name or ""
        if name:
            name = "--name=" + name

        if env:
            env = " ".join("--env {0}={1}".format(k, v) for k, v in env.items())

        if isinstance(volumes, collections.Mapping):
            volumes = " ".join("--volume %s:%s" % (k, volumes[k]) for k in volumes)

        cmd = "{docker} run {options} {name} {volumes} {env} {image} {command}".format(
            docker=self.binary,
            options=options or "",
            name=name,
            volumes=volumes or "",
            env=env or "",
            image=self.image,
            command=command,
        )

        return SubprocessRunner.callAndReturnResultWithoutOutput(cmd, shell=True)

    def subprocessRunnerFor(self, dockerargs, commands, onOut, onErr):
        return SubprocessRunner.SubprocessRunner(
            [self.binary, "run"] + dockerargs + [self.image] + commands, onOut, onErr
        )

    def stop(self, container_name):
        return SubprocessRunner.callAndReturnResultWithoutOutput(
            "{docker} stop {name}".format(docker=self.binary, name=container_name),
            shell=True,
        )

    def remove(self, container_name):
        return SubprocessRunner.callAndReturnResultWithoutOutput(
            "{docker} rm {name}".format(docker=self.binary, name=container_name),
            shell=True,
        )
