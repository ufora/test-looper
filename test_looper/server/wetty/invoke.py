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
import yaml
import shutil

import test_looper.core.algebraic_to_json as algebraic_to_json
import test_looper.core.tools.Git as Git
import test_looper.core.source_control.SourceControlFromConfig as SourceControlFromConfig
import test_looper.core.ArtifactStorage as ArtifactStorage
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.worker.WorkerState as WorkerState
import test_looper.core.tools.Docker as Docker
import test_looper.core.tools.DockerWatcher as DockerWatcher
import test_looper.core.cloud.MachineInfo as MachineInfo

own_dir = os.path.split(os.path.abspath(__file__))[0]

def createArgumentParser():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        'config', 
        help="server config.json file"
        )

    parser.add_argument(
        dest='repoName',
        help="The repo to run from"
        )

    parser.add_argument(
        dest='commitHash',
        help="The commitHash or branchname to use"
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

    repoName = args.repoName
    commitHash = args.commitHash

    print "***********************************"
    print "WELCOME TO TEST LOOPER"
    print "***********************************"
    print "invoking " + args.environment + " on source from " + repoName + "/" + commitHash
    
    config = loadConfiguration(args.config)

    temp_dir = tempfile.mkdtemp()

    artifactStorage = ArtifactStorage.storageFromConfig(config['artifacts'])

    source_control = SourceControlFromConfig.getFromConfig(config["source_control"])

    validHexChars = '0123456789abcdefABCDEF'
    if not (len(commitHash) == 40 and not [c for c in commitHash if c not in validHexChars]):
        source_control.getRepo(repoName).refresh()
        commitHash = source_control.getRepo(repoName).branchTopCommit(commitHash)

    workerState = WorkerState.WorkerState(
        "interactive_" + str(uuid.uuid4()) + "_",
        temp_dir,
        source_control,
        artifactStorage=artifactStorage,
        machineInfo=MachineInfo.MachineInfo("localhost",
                                          "localhost",
                                          1,
                                          "none",
                                          "bare metal"
                                          )
        )

    workerState.useRepoCacheFrom(os.path.join(os.path.expandvars(config['worker']['path']),"1"))

    try:
        testDef = workerState.testDefinitionFor(repoName, commitHash, args.environment)
        if testDef is None:
            raise Exception("Couldn't find test environment %s in %s/%s" % (args.environments, repoName, commitHash))

        if testDef.matches.Build:
            cmd = testDef.buildCommand
        elif testDef.matches.Test:
            cmd = testDef.testCommand
        elif testDef.matches.Deploy:
            cmd = testDef.deployCommand
        else:
            raise Exception("Unknown test definition type: " + str(testDef))

        environment, dependencies = workerState.getEnvironmentAndDependencies(repoName, commitHash, testDef)

        print yaml.dump(
            algebraic_to_json.Encoder().to_json(
                {'environment': environment, 'dependencies': dependencies}
                )
            )

        image = workerState.getDockerImage(environment)

        workerState.resetToCommit(repoName, commitHash)

        if not image:
            raise Exception("Couldn't get a valid docker image")

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

        env_vars = workerState.environment_variables(None, repoName, commitHash, environment, testDef)

        print "***********************************"
        for i in xrange(10):
            print

        shutil.copytree(
            os.path.join(own_dir, "exposed_in_invoke"), 
            os.path.join(temp_dir, "exposed_in_invoke")
            )
        with open(os.path.join(temp_dir, "exposed_in_invoke", "cmd.sh"), "w") as f:
            print >> f, "rm -rf /repo/.git"
            print >> f, "cp /exposed_in_invoke/fancy_bashrc ~/.bashrc"
            print >> f, cmd
            print >> f, "echo"
            print >> f, "echo"
            print >> f, "echo"
            print >> f, "echo"
            print >> f, "echo"
            print >> f, "echo you are now interactive"
            print >> f, "bash"

        try:
            bash_args = ['/exposed_in_invoke/cmd.sh']

            volumes = workerState.volumesToExpose()
            volumes[os.path.join(temp_dir, "exposed_in_invoke")] = "/exposed_in_invoke"

            with DockerWatcher.DockerWatcher(workerState.name_prefix) as watcher:
                container = watcher.run(image, 
                    ["bash"] + bash_args, 
                    privileged=True,
                    shm_size="1G",
                    stdin_open=True,
                    working_dir="/test_looper/src",
                    volumes=volumes,
                    environment=env_vars,
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

    except Exception as e:
        print "********************************"
        print
        print
        print
        import traceback
        traceback.print_exc()
        sys.exit(1)


