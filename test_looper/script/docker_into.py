#!/usr/bin/env python

import argparse
import os
import sys
import subprocess
import dockerpty
import docker
import uuid
import test_looper.core.tools.Docker as Docker
import test_looper.core.tools.DockerWatcher as DockerWatcher

def createArgumentParser():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        'dockerfile', 
        help="The Dockerfile used to build the image"
        )

    parser.add_argument(
        '-p',
        '--path',
        dest='path',
        default=None,
        help="Path to expose as /repo. Defaults to the root of the git repo containing the dockerfile."
        )

    parser.add_argument(
        '--port',
        dest='ports',
        action='append',
        help="ports to expose"
        )
    
    parser.add_argument(
        '--output',
        dest='output',
        default=None,
        help="directory to map to /output"
        )
    
    parser.add_argument(
        '--scratch',
        dest='scratch',
        default=None,
        help="directory to map to /scratch"
        )
    
    parser.add_argument(
        "commands",
        nargs=argparse.REMAINDER,
        help="Commands to run before becoming an interactive session."
        )
    
    return parser

def git_repo_containing(path):
    path = os.path.split(os.path.abspath(path))[0]
    while path and not os.path.exists(os.path.join(path,".git")):
        path = os.path.split(path)[0]
    return path or None

if __name__ == "__main__":
    args = createArgumentParser().parse_args()
    
    if args.path:
        repo = args.path
    else:
        repo = git_repo_containing(args.dockerfile)

    with open(args.dockerfile,"rb") as f:
        dockerfile_contents = f.read()

    image = Docker.DockerImage.from_dockerfile_as_string(None, dockerfile_contents, create_missing=True)
    
    print "Built docker image successfully. Image name is %s" % image.image

    bash_args = []
    if args.commands:
        bash_args = ["-c", " ".join(args.commands) + "; bash"]

    if args.ports is None:
        args.ports = {}
    else:
        args.ports = dict([v.split(":") for v in args.ports])

    with DockerWatcher.DockerWatcher("interactive_" + str(uuid.uuid4())) as watcher:
        container = watcher.run(image, 
            ["bash"] + bash_args, 
            privileged=True,
            shm_size="1G",
            stdin_open=True,
            working_dir="/repo",
            volumes=dict([
                (repo, "/repo"),
                ("/home/%s/.bash_history" % os.getenv("USER"), "/root/.bash_history")
                ] + 
                ([(args.output, "/output")] if args.output else []) + 
                ([(args.scratch, "/scratch")] if args.scratch else [])
                ),
            environment=dict([("TEST_SRC_DIR","/repo")] + 
                ([("TEST_OUTPUT_DIR", "/output")] if args.output else []) + 
                ([("TEST_SCRATCH_DIR", "/scratch")] if args.scratch else [])
                ),
            ports=args.ports,
            tty=True
            )
        client = docker.from_env()
        client.__dict__["inspect_container"] = lambda c: client.api.inspect_container(c.id)
        client.__dict__["attach_socket"] = lambda c,*args,**kwds: client.api.attach_socket(c.id, *args, **kwds)
        client.__dict__["resize"] = lambda c,*args,**kwds: client.api.resize(c.id, *args, **kwds)
        dockerpty.start(client, container)
