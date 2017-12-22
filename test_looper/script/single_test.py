#!/usr/bin/env python
import tempfile
import argparse
import os
import sys
import subprocess
import shutil
import uuid
import test_looper.core.tools.Docker as Docker
import test_looper.worker.WorkerState as WorkerState
import test_looper.core.tools.Git as Git
import test_looper.core.ArtifactStorage as ArtifactStorage
import test_looper.core.SubprocessRunner as SubprocessRunner
import test_looper.core.machine_management.MachineManagement as MachineManagement

def createArgumentParser():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        'test_definitions', 
        help="testDefinitions.json to run with"
        )
    parser.add_argument(
        'test_name', 
        help="testDefinitions.json to run with"
        )
    parser.add_argument(
        '-w',
        dest="workdir",
        default=None,
        help="working directory"
        )
    
    return parser

def git_repo_containing(path):
    path = os.path.split(os.path.abspath(path))[0]
    while path and not os.path.exists(os.path.join(path,".git")):
        path = os.path.split(path)[0]
    return path or None

if __name__ == "__main__":
    args = createArgumentParser().parse_args()
    
    td_path = os.path.abspath(args.test_definitions)
    repo = os.path.abspath(git_repo_containing(td_path))

    relpath = os.path.relpath(td_path, repo)

    workdir = os.path.abspath(args.workdir or tempfile.mkdtemp())

    try:
        artifacts = ArtifactStorage.LocalArtifactStorage({
            "build_storage_path": os.path.join(workdir, "artifacts", "build_artifacts"),
            "test_artifacts_storage_path": os.path.join(workdir, "artifacts", "test_artifacts")
            })

        worker = WorkerState.WorkerState(
            "test_looper_singleton",
            os.path.join(workdir, "worker"), 
            Git.LockedGit(repo),
            relpath,
            artifactStorage=artifacts,
            machineId="machineId",
            MachineManagement.HardwareConfig(cores=1,ram_gb=4)
            )

        worker.verbose = True

        if args.test_name != "build":
            print "Beginning build."
            if not worker.runTest("test", "<working_copy>", "build", lambda *args: None).success:
                print "Failed to build!"
                sys.exit(1)

        print "starting test ", args.test_name
        test_id = "test_" + str(uuid.uuid4())
        print "TEST_ID is ", test_id

        if not worker.runTest(test_id, "<working_copy>", args.test_name, lambda *args: None).success:
            print "Failed to run!"
            sys.exit(1)

        sys.exit(0)
    finally:
        if args.workdir is None:
            shutil.rmtree(workdir)
