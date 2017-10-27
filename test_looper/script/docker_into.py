#!/usr/bin/env python

import argparse
import os
import sys
import subprocess
import dockerpty
import docker
import test_looper.core.tools.Docker as Docker
import test_looper.core.tools.DockerWatcher as DockerWatcher

def createArgumentParser():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        'dockerfile', 
        help="The Dockerfile used to build the image"
        )
    
    return parser

def git_repo_containing(path):
    path = os.path.split(os.path.abspath(path))[0]
    while path and not os.path.exists(os.path.join(path,".git")):
        path = os.path.split(path)[0]
    return path or None

if __name__ == "__main__":
    args = createArgumentParser().parse_args()
    
    repo = git_repo_containing(args.dockerfile)

    with open(args.dockerfile,"rb") as f:
        dockerfile_contents = f.read()

    image = Docker.DockerImage.from_dockerfile_as_string(None, dockerfile_contents, create_missing=True)
    
    print "Built docker image successfully. Image name is %s" % image.image

    with DockerWatcher.DockerWatcher("interactive_") as watcher:
        container = watcher.run(image, 
            ["bash"], 
            privileged=True,
            shm_size="1G",
            stdin_open=True,
            working_dir="/repo",
            volumes={repo:"/repo"},
            environment={"TEST_SRC_DIR":"/repo"},
            tty=True
            )
        client = docker.from_env()
        client.__dict__["inspect_container"] = lambda c: client.api.inspect_container(c.id)
        client.__dict__["attach_socket"] = lambda c,*args,**kwds: client.api.attach_socket(c.id, *args, **kwds)
        client.__dict__["resize"] = lambda c,*args,**kwds: client.api.resize(c.id, *args, **kwds)
        dockerpty.start(client, container)
