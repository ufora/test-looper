#!/usr/bin/python2.7

import argparse
import random
import tempfile
import os
import sys
import subprocess
import dockerpty
import docker
import json
import simplejson
import uuid
import signal

import test_looper.core.tools.Git as Git
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.worker.WorkerState as WorkerState
import test_looper.core.tools.Docker as Docker
import test_looper.core.tools.DockerWatcher as DockerWatcher

own_dir = os.path.split(os.path.abspath(__file__))[0]

def createArgumentParser():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        'config', 
        help="server config.json file"
        )

    parser.add_argument(
        dest='commit',
        help="The commit to use"
        )

    parser.add_argument(
        dest='environment',
        help="The test or environment to invoke"
        )

    parser.add_argument(
        '--ports',
        dest='ports',
        default=None,
        help="comma-separated list of ports to expose"
        )
    
    return parser

def loadConfiguration(configFile):
    with open(configFile, 'r') as fin:
        return json.loads(fin.read())

def get_used_ports():
    used = subprocess.check_output("lsof -i -P -n | grep LISTEN", shell=True)
    results = []
    for u in used:
        try:
            results.append(int(u.split(":")[1].split( )[0]))
        except:
            pass
    return results

def pick_random_open_port():
    while True:
        new_port = 3000 + int(10000 * random.random())
        if new_port not in used_ports:
            return new_port


if __name__ == "__main__":
    args = createArgumentParser().parse_args()

    repoName, commitHash = args.commit.split("/")

    print "***********************************"
    print "WELCOME TO TEST LOOPER"
    print "***********************************"
    print "invoking " + args.environment + " on source from " + args.commit
    
    config = loadConfiguration(args.config)

    if "path_to_repos" in config["source_control"]:
        path = config["source_control"]["path_to_repos"]
    else:
        path = config["source_control"]["path_to_local_repos"]

    path = os.path.expandvars(path)

    repo = Git.Git(os.path.join(path, repoName))

    test_def_path = repo.getTestDefinitionsPath(commitHash)

    if test_def_path is None:
        print "********************************"
        print
        print
        print
        print "Couldn't find a testDefinitions.json/yaml file in the repo."
        print os.path.listdir(path)
        print
        sys.exit(1)

    testDefsTxt = repo.getFileContents(commitHash, test_def_path)

    if testDefsTxt is None:
        print "********************************"
        print
        print
        print
        print "Couldn't find a testDefinitions.jsonyaml file in the repo."
        print os.path.listdir(path), test_def_path
        print
        sys.exit(1)

    testDefs = TestDefinitionScript.extract_tests_from_str(testDefsText)

    testDef = testDefs.get(args.environment)

    if not testDef:
        print "********************************"
        print
        print
        print
        print "Couldn't find " + args.environment + " in testDefinitions:\n\n" + testDefsTxt
        print
        sys.exit(1)

    if testDef.matches.Build:
        cmd = testDef.buildCommand
    elif testDef.matches.Test:
        cmd = testDef.testCommand
    elif testDef.matches.Deploy:
        cmd = testDef.deployCommand
    else:
        print "********************************"
        print
        print
        print
        print "Unknown test definition type: " + str(testDef)
        print


    temp_dir = tempfile.mkdtemp()

    repo_dir = os.path.join(temp_dir, "repo")

    repo.resetToCommitInDirectory(commitHash, os.path.join(temp_dir, "repo"))

    if args.ports:
        type_and_port = args.ports.split(",")
        used_ports = get_used_ports()
        ports = {}

        for tp in type_and_port:
            type, port = tp.split(":")
            tgt = pick_random_open_port()
            ports[port] = tgt

            print "Exposed %s=%s in container on host as %s" % (type, port, tgt)
    else:
        ports = {}

    print "***********************************"
    for i in xrange(10):
        print

    try:
        image = WorkerState.WorkerState.getDockerImageFromRepo(repo, commitHash, testDef.docker)
            
        bash_args = ["-c", "rm -rf /repo/.git; " + cmd + '; cp /exposed_in_invoke/fancy_bashrc ~/.bashrc; echo "\n\n\n\n\nYou are now interactive\n\n"; bash']

        with DockerWatcher.DockerWatcher("interactive_" + str(uuid.uuid4()) + "_") as watcher:
            container = watcher.run(image, 
                ["bash"] + bash_args, 
                privileged=True,
                shm_size="1G",
                stdin_open=True,
                working_dir="/repo",
                volumes={repo_dir:"/repo", os.path.join(own_dir, "exposed_in_invoke"): "/exposed_in_invoke"},
                environment={"TEST_SRC_DIR":"/repo"},
                ports=ports,
                tty=True
                )
            
            client = docker.from_env()
            client.__dict__["inspect_container"] = lambda c: client.api.inspect_container(c.id)
            client.__dict__["attach_socket"] = lambda c,*args,**kwds: client.api.attach_socket(c.id, *args, **kwds)
            client.__dict__["resize"] = lambda c,*args,**kwds: client.api.resize(c.id, *args, **kwds)

            def handleStopSignal(signum, _):
                print "Shutting down."
                container.stop()

            signal.signal(signal.SIGTERM, handleStopSignal) # handle kill
            signal.signal(signal.SIGINT, handleStopSignal)  # handle ctrl-c
            signal.signal(signal.SIGHUP, handleStopSignal)  # handle sighup

            dockerpty.start(client, container)
    finally:
        WorkerState.WorkerState.clearDirectoryAsRoot(temp_dir)
