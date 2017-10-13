#!/usr/bin/env python

import argparse
import os
import test_looper.core.tools.Docker as Docker

def createArgumentParser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-r',
        '--docker_repo', 
        help="The docker repo we should push to / search in",
        default=None
        )
    parser.add_argument(
        '-c',
        '--create_missing', 
        help="Create the repo if it's not in there already",
        action='store_true',
        default=False
        )
    parser.add_argument(
        'dockerfile', 
        help="The Dockerfile used to build the image"
        )
    parser.add_argument(
        'args',
        nargs=argparse.REMAINDER
        )

    return parser

if __name__ == "__main__":
    args = createArgumentParser().parse_args()
    
    with open(args.dockerfile,"rb") as f:
        dockerfile_contents = f.read()

    image = Docker.DockerImage.from_dockerfile_as_string(args.docker_repo, dockerfile_contents, create_missing=args.create_missing)
    
    print "Built docker image successfully. Image name is %s" % image.image

    to_run = ['bash']

    dockerargs = ["-it", "--rm", "-v", "/var/run/docker.sock:/var/run/docker.sock", "--net=host"] + args.args

    image.run(" ".join(to_run), options = " ".join(dockerargs))
