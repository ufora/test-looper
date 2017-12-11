#!/usr/bin/env python

import argparse
import sys
import os
import traceback

import test_looper.core.ArtifactStorage as ArtifactStorage


def createArgumentParser():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target_dir',help="On-disk directory containing test data")
    parser.add_argument('--bucket',help="Bucket containing test data")
    parser.add_argument('--region',help="AWS region to connect to", default="us1-east")
    parser.add_argument('--dir',help="directory to upload", required=True)
    parser.add_argument('--name',help="name of the artifact (if not the dirname)", default=None)

    return parser

if __name__ == "__main__":
    args = createArgumentParser().parse_args()
    
    config = {}
    if args.target_dir:
        config['type'] = 'local_disk'
        config['data_storage_path'] = args.target_dir
        config['build_storage_path'] = None
        config['test_artifacts_storage_path'] = None
    else:
        config['type'] = 's3'
        config['aws_region'] = args.region
        config['bucket'] = args.bucket

    artifactStorage = ArtifactStorage.storageFromConfig(config)

    name = args.name
    if not name:
        name = os.path.split(args.dir)[-1]

    try:
        shahash = artifactStorage.create_data_artifact(args.dir, name)

        print "Successfully uploaded artifact as %s / %s" % (name, shahash)
    except:
        import traceback
        traceback.print_exc()
        sys.exit(1)
