#!/usr/bin/env python

import argparse
import os
import sys
import subprocess
import test_looper.core.tools.Docker as Docker

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

    subprocess.call(
        "docker run -it --privileged --rm --net=host --shm-size=1g -w /repo -v {repo}:/repo -v /var/run:/var/run -e TEST_SRC_DIR=/repo {image}"
            .format(repo=repo,image=image.image),
        shell=True,
        stdout=sys.stdout,
        stderr=sys.stderr
        )

