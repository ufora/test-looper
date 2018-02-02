#!/usr/bin/env python

import argparse
import json
import traceback
import logging
import os
import signal
import socket
import sys
import threading
import time
import shutil
import select
import tty
import termios

proj_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.append(proj_root)

import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.core.tools.Git as Git
import test_looper.core.Config as Config
import test_looper.worker.WorkerState as WorkerState
import test_looper.core.tools.Docker as Docker
import yaml

def configureLogging(verbose=False):
    loglevel = logging.INFO if verbose else logging.WARN
    logging.getLogger().setLevel(loglevel)

    for handler in logging.getLogger().handlers:
        handler.setLevel(loglevel)
        handler.setFormatter(
            logging.Formatter(
                '%(asctime)s %(levelname)s %(filename)s:%(lineno)s@%(funcName)s %(name)s - %(message)s'
                )
            )

def createArgumentParser():
    parser = argparse.ArgumentParser(
        description="Checkout multi-repo projects locally and run tests using docker"
        )

    parser.add_argument("-v",
                        "--verbose",
                        dest="verbose",
                        default=False,
                        action='store_true',
                        help="Set logging level to verbose")

    subparsers = parser.add_subparsers()

    clear_parser = subparsers.add_parser("clear")
    clear_parser.set_defaults(command="clear")
    clear_parser.add_argument("-b", "--build", help="Get info on a specific test", default=False, action="store_true")
    clear_parser.add_argument("-s", "--src", help="Get info on a specific test", default=False, action="store_true")

    init_parser = subparsers.add_parser("init")
    init_parser.set_defaults(command="init")
    init_parser.add_argument("path", help="Path to disk storage")
    init_parser.add_argument("git_clone_root", help="Git clone root (e.g. git@gitlab.mycompany.com)")

    checkout_parser = subparsers.add_parser("checkout")
    checkout_parser.set_defaults(command="checkout")
    checkout_parser.add_argument("repo", help="Name of the repo")
    checkout_parser.add_argument("committish", help="Name of the commit or branch")

    build_parser = subparsers.add_parser("build")
    build_parser.set_defaults(command="build")
    build_parser.add_argument("repo", help="Name of the repo")
    build_parser.add_argument("test", help="Name of the test")
    build_parser.add_argument("-i", "--interactive", dest="interactive", default=False, help="Drop into an interactive terminal", action="store_true")
    build_parser.add_argument("-d", "--nodeps", dest="nodeps", default=False, help="Don't build dependencies, just this one", action="store_true")
    build_parser.add_argument("-s", "--nologcapture", dest="nologcapture", default=False, help="Don't capture logs - show directly", action="store_true")
    build_parser.add_argument("-j",
                        "--cores",
                        dest="cores",
                        default=1,
                        type=int,
                        help="Number of cores to expose")

    info_parser = subparsers.add_parser("info")
    info_parser.set_defaults(command="info")
    info_parser.add_argument("-t", "--test", help="Get info on a specific test", default=None, required=False)
    info_parser.add_argument("-r", "--repo", help="Get info on a checked-out repo", default=None, required=False)

    return parser

def loadConfiguration(configFile):
    with open(configFile, 'r') as fin:
        expanded = os.path.expandvars(fin.read())
        return json.loads(expanded)

def find_cur_root(path):
    path = os.path.abspath(path)
    
    while True:
        if os.path.exists(os.path.join(path, ".tl")):
            return path

        subpath = os.path.dirname(path)
        if subpath == path:
            return None
        path = subpath

def run_init(args):
    curRoot = find_cur_root(os.getcwd())
    if curRoot:
        raise UserWarning("Can't initialize a tl directory here. There is already one at\n\n%s" % curRoot)

    root = os.getcwd()
    os.mkdir(os.path.join(root, ".tl"))

    config_file = os.path.join(root, ".tl", "config.yml")
    with open(config_file, "w") as f:
        f.write(yaml.dump({"git_clone_root": args.git_clone_root}, indent=4, default_style=''))



class DummyArtifactStorage(object):
    def __init__(self):
        object.__init__(self)

    def upload_build(self, repoName, commitHash, key_name, file_name):
        pass

    def build_exists(self, repoName, commitHash, key_name):
        pass

    def uploadSingleTestArtifact(self, repoName, commitHash, testId, artifact_name, path):
        pass

    def uploadTestArtifacts(self, *args, **kwargs):
        pass

class WorkerStateOverride(WorkerState.WorkerState):
    def __init__(self, name_prefix, worker_directory, looperCtl, cores):
        hwConfig = Config.HardwareConfig(cores=cores, ram_gb=8)

        WorkerState.WorkerState.__init__(self, name_prefix, worker_directory, None, DummyArtifactStorage(), "machine", hwConfig)
        
        self.looperCtl = looperCtl
        self.extra_mappings = {}
        self.resolver = looperCtl.resolver

    def wants_to_run_cleanup(self):
        return False

    def getRepoCacheByName(self, name):
        return self.looperCtl.getGitRepo(name)

    def resetToCommitInDir(self, repoName, commitHash, targetDir):
        assert False

    def cleanup(self):
        if Docker is not None:
            Docker.DockerImage.removeDanglingDockerImages()

        #don't remove everything!
        self.clearDirectoryAsRoot(
            self.directories.scratch_dir, 
            self.directories.test_inputs_dir, 
            self.directories.command_dir
            )

    def volumesToExpose(self):
        res = {
            self.directories.scratch_dir: "/test_looper/scratch",
            self.directories.test_output_dir: "/test_looper/output",
            self.directories.build_output_dir: "/test_looper/build_output",
            self.directories.ccache_dir: "/test_looper/ccache",
            self.directories.command_dir: "/test_looper/command"
            }
        res.update(self.extra_mappings)

        return res
    
    def _upload_build(self, *args, **kwargs):
        return True

    def resetToCommit(self, repoName, commitHash):
        self.extra_mappings[self.looperCtl.repo_and_commit_checkout_root_path(repoName, commitHash)] = \
            "/test_looper/src"

        return True

    def grabDependency(self, log_function, expose_as, dep, repoName, commitHash):
        target_dir = os.path.join(self.directories.test_inputs_dir, expose_as)

        if dep.matches.InternalBuild or dep.matches.ExternalBuild:
            if dep.matches.ExternalBuild:
                repoName, commitHash = dep.repo, dep.commitHash

            self.extra_mappings[
                os.path.join(self.looperCtl.build_path(repoName, commitHash, dep.name), "build_output")
                ] = os.path.join("/test_looper/test_inputs", expose_as)

            return None

        if dep.matches.Source:
            self.extra_mappings[
                self.looperCtl.repo_and_commit_checkout_root_path(dep.repo, dep.commitHash)
                ] = os.path.join("/test_looper/test_inputs", expose_as)

            return None

        return "Unknown dependency type: %s" % dep


class TestDefinitionResolverOverride(WorkerState.TestDefinitionResolver):
    def __init__(self, looperCtl):
        WorkerState.TestDefinitionResolver.__init__(self, looperCtl.getGitRepo)
        self.looperCtl = looperCtl

    def testAndEnvironmentDefinitionFor(self, repoName, commitHash):
        if commitHash in self.looperCtl.cur_checkouts.get(repoName, []):
            root_path = self.looperCtl.repo_and_commit_checkout_root_path(repoName, commitHash)

            path = Git.Git.getTestDefinitionsPathFromDir(root_path)

            if not path:
                return {}, {}, {}

            text = open(os.path.join(root_path, path), "r").read()

            return TestDefinitionScript.extract_tests_from_str(repoName, commitHash, os.path.splitext(path)[1], text)

        return WorkerState.TestDefinitionResolver.testAndEnvironmentDefinitionFor(self, repoName, commitHash)

class TestLooperCtl:
    def __init__(self, root_path, config):
        self.root_path = root_path
        self.git_clone_root = config["git_clone_root"]
        self.repos = {}
        self.resolver = TestDefinitionResolverOverride(self)

        self.cur_checkouts = config.get("cur_checkouts", {})

    def sanitize(self, name):
        return name.replace("/","_").replace(":","_")

    def build_path(self, reponame, commit, testname):
        return os.path.abspath(os.path.join(self.root_path, "builds", self.sanitize(reponame + "_" + commit + "_" + testname)))

    def repo_checkout_root_path(self, reponame):
        return os.path.abspath(os.path.join(self.root_path, "src", self.sanitize(reponame)))

    def repo_and_commit_checkout_root_path(self, reponame, hash):
        base = os.path.abspath(os.path.join(self.root_path, "src", self.sanitize(reponame)))
        
        if len(self.cur_checkouts[reponame]) == 1:
            return base

        return os.path.join(base, hash)
    
    def writeStateToConfig(self):
        config = {
            "git_clone_root": self.git_clone_root,
            "cur_checkouts": self.cur_checkouts
            }
        with open(os.path.join(self.root_path, ".tl", "config.yml"), "w") as f:
            f.write(yaml.dump(config, indent=4, default_style=''))

    def getGitRepo(self, reponame):
        if reponame in self.repos:
            return self.repos[reponame]
        
        self.repos[reponame] = Git.Git(os.path.join(*([self.root_path, ".tl", "repos"] + reponame.split("/"))))

        if not self.repos[reponame].isInitialized():
            clone_root = self.git_clone_root + ":" + reponame + ".git"
            print "Cloning " + clone_root

            if not self.repos[reponame].cloneFrom(clone_root):
                del self.repos[reponame]
                raise UserWarning("Failed to clone " + reponame)

        return self.repos[reponame]

    def clearDirectoryAsRoot(self, *args):
        image = Docker.DockerImage("ubuntu:16.04")
        image.run(
            "rm -rf " + " ".join(["%s/*" % p for p in args]), 
            volumes={a:a for a in args}, 
            options="--rm"
            )

    def clear(self, args):
        if args.src:
            self.cur_checkouts = {}
            self.clearDirectoryAsRoot(os.path.join(self.root_path, "src"))

        if args.build:
            self.clearDirectoryAsRoot(os.path.join(self.root_path, "builds"))

    def checkout(self, args):
        reponame = self.bestRepo(args.repo)

        repo = self.getGitRepo(reponame)
        
        committish = args.committish

        repo.fetchOrigin()

        if not Git.isShaHash(committish):
            committish = repo.hashParentsAndCommitTitleFor("origin/" + committish)[0]

        repo_usages = {}

        def resolve(reponame, committish):
            #now find all the dependent repos and make sure we have them as well
            _,_,repos = self.resolver.testAndEnvironmentDefinitionFor(reponame, committish)

            for v in repos.values():
                import_repo_ref(v.reference)

        def import_repo_ref(reference):
            reponame = "/".join(reference.split("/")[:-1])
            hash = reference.split("/")[-1]

            if reponame not in repo_usages:
                repo_usages[reponame] = [hash]
                is_new = True
            else:
                is_new = hash not in repo_usages[reponame]

                if is_new:
                    repo_usages[reponame].append(hash)

            if is_new:
                resolve(reponame, hash)

        import_repo_ref(reponame + "/" + committish)

        for reponame in sorted(repo_usages):
            self._checkoutRepoNames(reponame, repo_usages[reponame])

    def _checkoutRepoNames(self, reponame, hashes):
        path = self.repo_checkout_root_path(reponame)

        if reponame not in self.cur_checkouts:
            self.cur_checkouts[reponame] = []

        if self.cur_checkouts[reponame] == hashes:
            return
        
        if len(hashes) == 1 and len(self.cur_checkouts[reponame]) == 1:
            print "Checking out ", reponame + "/" + hashes[0][:10], " in ", path

            self.cur_checkouts[reponame] = []
            Git.Git(path).resetToCommit(hashes[0])
            self.cur_checkouts[reponame] = hashes
        else:
            if os.path.exists(path):
                shutil.rmtree(path)

            self.cur_checkouts[reponame] = []

            if len(hashes) != 1:
                for h in hashes:
                    self.getGitRepo(reponame).resetToCommitInDirectory(h, os.path.join(path, h))
            else:
                print "Checking out ", reponame + "/" + hashes[0][:10], " in ", path
                self.getGitRepo(reponame).resetToCommitInDirectory(hashes[0], path)

            self.cur_checkouts[reponame] = hashes

    def commitForRepo(self, reponame):
        if reponame not in self.cur_checkouts:
            raise UserWarning("Can't find " + reponame + " amongst checked out repos %s" % sorted(self.cur_checkouts))

        if len(self.cur_checkouts[reponame]) != 1:
            raise UserWarning("Repo " + reponame + " has multiple revisions checked out.")

        return self.cur_checkouts[reponame][0]

    def info(self, args):
        if args.repo:
            if args.test:
                self.infoForTest(args.repo, args.test)
            else:
                self.infoForRepo(args.repo)
            return


        raise UserWarning("Nothing specified.")

    def infoForTest(self, repo, test):
        repo = self.bestRepo(repo)
        commit = self.commitForRepo(repo)
        test = self.bestTest(repo, commit, test)

        tests, environments, repos = self.resolver.testAndEnvironmentDefinitionFor(repo, commit)

        if test not in tests:
            raise UserWarning("Can't find test %s" % test)

        testDef = tests[test]

        print "test: ", test

        print "dependencies: "
        for depname, dep in sorted(testDef.dependencies.iteritems()):
            if dep.matches.InternalBuild:
                print "\tbuild: ", dep.name
            if dep.matches.ExternalBuild:
                print "\tbuild: ", dep.repo + "/" + dep.commitHash + "/" + dep.name
            if dep.matches.Source:
                print "\tsource:", dep.repo + "/" + dep.commitHash

    def infoForRepo(self, repo):
        repo = self.bestRepo(repo)

        commit = self.commitForRepo(repo)

        tests, environments, repos = self.resolver.testAndEnvironmentDefinitionFor(repo, commit)

        print "repo: ", repo, "checked out to", commit

        print "\tbuilds: "
        for test, testDef in tests.iteritems():
            if testDef.matches.Build:
                print "\t\t", test

        print "\ttests: "
        for test, testDef in tests.iteritems():
            if testDef.matches.Test:
                print "\t\t", test

    def bestRepo(self, reponame):
        if reponame in self.cur_checkouts:
            return reponame
        return self._pickOne(reponame, self.cur_checkouts, "repo") or reponame

    def _pickOne(self, lookfor, possibilities, kindOfThing):
        possible = [item for item in possibilities if lookfor in item]
        if len(possible) == 1:
            return possible[0]
        if len(possible) > 1:
            raise UserWarning("%s could refer to %s of %s" % (lookfor, "any" if len(possible) > 2 else "either", possible))
        return None

    def bestTest(self, reponame, commit, test):
        tests = self.resolver.testAndEnvironmentDefinitionFor(reponame, commit)[0]

        res = self._pickOne(test, tests, "test")
        if not res:
            raise UserWarning("Couldn't find a test named %s" % test)
        return res

    def build(self, args):
        repo = self.bestRepo(args.repo)

        commit = self.commitForRepo(repo)

        test = self.bestTest(repo, commit, args.test)

        self.buildTest(repo, commit, test, args.cores, args.nologcapture, args.nodeps, args.interactive, set())

    def buildTest(self, reponame, commit, testname, cores, nologcapture, nodeps, interactive, seen_already):
        if interactive:
            if not nodeps:
                print "Interactive implies no dependencies."
            if not nologcapture:
                print "Interactive implies nologcapture"
            nologcapture = True
            nodeps = True

        path = self.build_path(reponame, commit, testname)

        if path in seen_already:
            return
        seen_already.add(path)

        all_tests = self.resolver.testAndEnvironmentDefinitionFor(reponame, commit)[0]

        if testname not in all_tests:
            raise UserWarning("Can't find test/build %s/%s/%s" % (reponame, commit, testname))

        testDef = all_tests[testname]

        if not nodeps:
            for depname, dep in testDef.dependencies.iteritems():
                if dep.matches.Source:
                    pass
                if dep.matches.InternalBuild:
                    if not self.buildTest(reponame, commit, dep.name, cores, nologcapture, nodeps, interactive, seen_already):
                        print "Dependent build ", reponame, commit, dep.name, " failed"
                        return False
                if dep.matches.ExternalBuild:
                    if not self.buildTest(dep.repo, dep.commitHash, dep.name, cores, nologcapture, nodeps, interactive, seen_already):
                        print "Dependent build ", dep.repo, dep.commitHash, dep.name, " failed"
                        return False
        
        print "Building ", reponame, commit, testname

        worker_state = WorkerStateOverride("test_looper_interactive_", path, self, cores)

        if nologcapture:
            logfile = sys.stdout
        else:
            logfile_dir = os.path.join(path, "logs")
            worker_state.ensureDirectoryExists(logfile_dir)
            t = time.gmtime()
            log_path = os.path.join(logfile_dir, "Log-%s-%s-%s-%s-%s-%s.txt" % (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec))
            logfile = open(log_path, "w")

            print "\tlogging output to ", log_path

        if not interactive:
            class Callbacks:
                def __init__(self):
                    self.t0 = time.time()
                    self.total_lines = 0

                def heartbeat(self, logMessage=None):
                    if logMessage:
                        logfile.write(logMessage)
                        self.total_lines += logMessage.count("\n")
                        if time.time() - self.t0 > 10 and not nologcapture:
                            print "\t", time.asctime(), " - ", self.total_lines, " logged"
                            self.t0 = time.time()
                            logfile.flush()

                def terminalOutput(self, output):
                    pass

                def subscribeToTerminalInput(self, callback):
                    pass
            callbacks = Callbacks()
        else:
            callbacks = WorkerState.DummyWorkerCallbacks(localTerminal=True)

        if not worker_state.runTest("interactive", reponame, commit, testname, callbacks, interactive)[0]:
            print "Build failed. Exiting."
            return False
        return True




def main(argv):
    parsedArgs = createArgumentParser().parse_args()
    configureLogging(verbose=parsedArgs.verbose)

    try:
        if parsedArgs.command == "init":
            run_init(parsedArgs)
        else:
            root = find_cur_root(os.getcwd())
            if not root:
                raise UserWarning("Not a tl path")
            
            try:
                config_file = os.path.join(root, ".tl", "config.yml")

                with open(config_file, "r") as f:
                    config = yaml.load(f.read())
            except Exception as e:
                raise UserWarning("Corrupt config file: " + str(e))

            ctl = TestLooperCtl(root, config)

            try:
                if parsedArgs.command == "checkout":
                    ctl.checkout(parsedArgs)
                elif parsedArgs.command == "clear":
                    ctl.clear(parsedArgs)
                elif parsedArgs.command == "info":
                    ctl.info(parsedArgs)
                elif parsedArgs.command == "build":
                    ctl.build(parsedArgs)
                else:
                    raise UserWarning("Unknown command " + parsedArgs.command)
            finally:
                ctl.writeStateToConfig()

    except UserWarning as e:
        print "Error:\n\n%s" % str(e)
        return 1    

    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
