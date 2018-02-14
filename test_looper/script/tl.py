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
import test_looper.data_model.TestDefinitionResolver as TestDefinitionResolver
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

    fetch_parser = subparsers.add_parser("fetch")
    fetch_parser.set_defaults(command="fetch")

    init_parser = subparsers.add_parser("init")
    init_parser.set_defaults(command="init")
    init_parser.add_argument("path", help="Path to disk storage")
    init_parser.add_argument("git_clone_root", help="Git clone root (e.g. git@gitlab.mycompany.com)")

    status_parser = subparsers.add_parser("status")
    status_parser.set_defaults(command="status")

    checkout_parser = subparsers.add_parser("checkout")
    checkout_parser.set_defaults(command="checkout")
    checkout_parser.add_argument("repo", help="Name of the repo")
    checkout_parser.add_argument("committish", help="Name of the commit or branch")
    checkout_parser.add_argument("--hard", help="Force a hard reset in the source repo", default=False, action="store_true")
    checkout_parser.add_argument("--prune", help="Get rid of unused repos", default=False, action="store_true")
    checkout_parser.add_argument("--from", help="Create a new branch, based on this one", dest="from_name", default=None)
    checkout_parser.add_argument("--orphan", help="Create a new orphaned branch, based on this one", dest="orphan", default=False, action='store_true')

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
        self.extra_mappings[self.looperCtl.checkout_root_path(repoName, commitHash)] = \
            "/test_looper/src"

        return True

    def grabDependency(self, log_function, expose_as, dep, repoName, commitHash, worker_callback):
        if expose_as is WorkerState.SOURCE_DIR:
            self.extra_mappings[self.looperCtl.checkout_root_path(repoName, commitHash)] = \
                "/test_looper/src"
        else:
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
                    self.looperCtl.checkout_root_path(dep.repo, dep.commitHash)
                    ] = os.path.join("/test_looper/test_inputs", expose_as)

                return None

            return "Unknown dependency type: %s" % dep


class TestDefinitionResolverOverride(TestDefinitionResolver.TestDefinitionResolver):
    def __init__(self, looperCtl):
        TestDefinitionResolver.TestDefinitionResolver.__init__(self, looperCtl.getGitRepo)
        self.looperCtl = looperCtl

    def testDefinitionTextAndExtensionFor(self, repoName, commitHash):
        if commitHash in self.looperCtl.cur_checkouts.get(repoName, []):
            root_path = self.looperCtl.checkout_root_path(repoName, commitHash)

            if os.path.exists(root_path):
                path = Git.Git.getTestDefinitionsPathFromDir(root_path)

                if not path:
                    return None

                text = open(os.path.join(root_path, path), "r").read()

                return text, os.path.splitext(path)[1]

        return TestDefinitionResolver.TestDefinitionResolver.testDefinitionTextAndExtensionFor(self, repoName, commitHash)

class TestLooperCtl:
    def __init__(self, root_path):
        self.root_path = root_path
        self.repos = {}
        self.resolver = TestDefinitionResolverOverride(self)
        self.initializeAllRepoNames()
    
        self._loadConfig()
        self._loadState()

    def _loadState(self):
        try:
            state_file_path = os.path.join(self.root_path, ".tl", "state.yml")

            if not os.path.exists(state_file_path):
                self.cur_checkouts = {}
                self.checkout_root = (None, None)
                return
        
            with open(state_file_path, "r") as f:
                state = yaml.load(f.read())

            #map from repo->hash->name of the current checkout
            self.cur_checkouts = state.get("cur_checkouts", {})

            #repo, branch (or None), commit
            self.checkout_root = state.get("checkout_root", (None, None))
        except Exception as e:
            raise UserWarning("Corrupt state file: " + str(e))

    def _loadConfig(self):
        try:
            config_file_path = os.path.join(self.root_path, ".tl", "config.yml")

            with open(config_file_path, "r") as f:
                config = yaml.load(f.read())

            self.repo_prefixes_to_strip = config.get("repo_prefixes_to_strip", [])
            self.repo_prefixes_to_ignore = config.get("repo_prefixes_to_ignore", [])

            self.git_clone_root = config["git_clone_root"]

        except Exception as e:
            raise UserWarning("Corrupt config file: " + str(e))

    def repoIsNotIgnored(self, repo):
        for r in self.repo_prefixes_to_ignore:
            if repo.startswith(r):
                return False
        return True

    def repoShortname(self, repo):
        prunes = [p for p in self.repo_prefixes_to_strip if repo.startswith(p)]
        prunes = sorted(prunes, key=len)

        if prunes:
            return repo[len(prunes[-1]):]
        return repo

    def initializeAllRepoNames(self):
        self.allRepoNames = set()

        def walk(items):
            dirpath = os.path.join(self.root_path, ".tl", "repos", *items)

            for i in os.listdir(dirpath):
                fullpath = os.path.join(dirpath, i)
                if i != ".git" and os.path.isdir(fullpath):
                    if os.path.exists(os.path.join(fullpath, ".git")):
                        self.allRepoNames.add("/".join(items + (i,)))
                    else:
                        walk(items + (i,))

        walk(())


    def fetch(self, args):
        print "fetching origin for ", len(self.allRepoNames), " repos..."

        threads = []
        for reponame in self.allRepoNames:
            def makeUpdater(name):
                def f():
                    try:
                        self.getGitRepo(name).fetchOrigin()
                    except:
                        logging.error("Failed to update repo %s: %s", name, traceback.format_exc())
                return f
            threads.append(threading.Thread(target=makeUpdater(reponame)))
            threads[-1].daemon=True
            threads[-1].start()

        for t in threads:
            t.join()



    def sanitize(self, name):
        return name.replace("/","_").replace(":","_").replace("~", "--")

    def build_path(self, reponame, commit, testname):
        buildname = self.cur_checkouts[reponame][commit]

        return os.path.abspath(os.path.join(self.root_path, "builds", self.sanitizeReponame(reponame), self.sanitize(buildname), self.sanitize(testname)))

    def sanitizeReponame(self, reponame):
        return self.sanitize(reponame)

    def checkout_root_path(self, reponame, hashOrCommitName):
        if hashOrCommitName is None:
            return os.path.abspath(os.path.join(*((self.root_path, "src") + tuple(reponame.split("/")))))

        if Git.isShaHash(hashOrCommitName):
            hashOrCommitName = self.cur_checkouts[reponame][hashOrCommitName]

        return os.path.abspath(os.path.join(*((self.root_path, "src") + tuple(reponame.split("/")) + (self.sanitize(hashOrCommitName),))))
    
    def writeState(self):
        config = {
            "cur_checkouts": self.cur_checkouts,
            "checkout_root": self.checkout_root
            }

        with open(os.path.join(self.root_path, ".tl", "state.yml"), "w") as f:
            f.write(yaml.dump(config, indent=4, default_style=''))

    def getGitRepo(self, reponame):
        if reponame in self.repos:
            return self.repos[reponame]
        
        self.repos[reponame] = Git.Git(os.path.join(*([self.root_path, ".tl", "repos"] + reponame.split("/"))))
        self.allRepoNames.add(reponame)

        if not self.repos[reponame].isInitialized():
            if not os.path.exists(self.repos[reponame].path_to_repo):
                os.makedirs(self.repos[reponame].path_to_repo)

            clone_root = self.git_clone_root + ":" + reponame + ".git"
            
            if not self.repos[reponame].cloneFrom(clone_root):
                del self.repos[reponame]
                if os.path.exists(clone_root):
                    shutil.rmtree(clone_root)
                return None
            else:
                print "Cloned " + clone_root + " into " + self.repos[reponame].path_to_repo
        
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

    def createNewBranchAndPush(self, repo, branchname, from_name):
        if from_name:
            repo.pushCommit("origin/" + from_name, branchname, force=False, create=True)
        else:
            hash = repo.createInitialCommit()
            repo.pushCommit(hash, branchname, force=False, createBranch=True)

    def checkout(self, args):
        reponame = self.bestRepo(args.repo)

        repo = self.getGitRepo(reponame)

        if repo is None:
            for path in self.repo_prefixes_to_strip:
                repo = self.getGitRepo(path + reponame)
                if repo:
                    break

        if not repo:
            raise UserWarning("Can't find repo %s" % reponame)
        
        committish = args.committish

        if args.from_name:
            self.createNewBranchAndPush(repo, committish, args.from_name)
        elif args.orphan:
            self.createNewBranchAndPush(repo, committish, None)

        if not Git.isShaHash(committish):
            committish = repo.gitCommitData("origin/" + committish)[0]

        self.checkout_root = (reponame, committish)

        repo_usages = {}

        def resolve(reponame, committish):
            #now find all the dependent repos and make sure we have them as well
            _,_,repos = self.resolver.testEnvironmentAndRepoDefinitionsFor(reponame, committish)

            for v in repos.values():
                import_repo_ref(v.reference)

        def import_repo_ref(reference):
            reponame = "/".join(reference.split("/")[:-1])
            hash = reference.split("/")[-1]

            if reponame not in repo_usages:
                repo_usages[reponame] = {}
                is_new = True
            else:
                is_new = hash not in repo_usages[reponame]

            if is_new:
                repo = self.getGitRepo(reponame)
                if not repo:
                    return

                repo_usages[reponame][hash] = repo.branchnameForCommitSloppy(hash)
                
                if repo_usages[reponame][hash] is None:
                    repo_usages[reponame][hash] = repo.branchnameForCommitSloppy(hash)

                if repo_usages[reponame][hash] is None:
                    repo_usages[reponame][hash] = hash[:10]

                resolve(reponame, hash)

        import_repo_ref(reponame + "/" + committish)

        for reponame in sorted(repo_usages):
            self._checkoutRepoNames(reponame, repo_usages[reponame], args.hard, args.prune)

        if args.prune:
            for reponame in self.allRepoNames:
                if reponame not in repo_usages:
                    print "pruning entire repo reference ", self.repoShortname(reponame)

                    self.clearDirectoryAsRoot(self.checkout_root_path(reponame, None))
                    shutil.rmtree(self.checkout_root_path(reponame, None))

        for reponame in sorted(repo_usages):
            for commit, commitName in self.cur_checkouts[reponame].iteritems():
                _,_,repos = self.resolver.testEnvironmentAndRepoDefinitionsFor(reponame, commit)

                if repos:
                    print "repo ", self.repoShortname(reponame), commitName
                    for refname, ref in repos.iteritems():
                        if self.repoIsNotIgnored(ref.reponame()):
                            print "\t", refname, " -> ", self.repoShortname(ref.reponame()), \
                                self.cur_checkouts[ref.reponame()].get(ref.commitHash(), ref.commitHash()[:10])

        for reponame in self.allRepoNames:
            if reponame not in repo_usages and reponame in self.cur_checkouts:
                del self.cur_checkouts[reponame]

    def _checkoutRepoNames(self, reponame, hashes, hard=False, prune=False):
        assert len(set(hashes.values())) == len(hashes), "Conflicting checkout names: " + str(hashes)

        if reponame not in self.cur_checkouts:
            self.cur_checkouts[reponame] = {}

        existing_name_checkouts = {v:k for k,v in self.cur_checkouts[reponame].items()}

        for hash, checkout_name in hashes.iteritems():
            path = self.checkout_root_path(reponame, checkout_name)

            if not Git.Git(path).isInitialized():
                print "Checkout commit ", hash, " to ", path
                self.getGitRepo(reponame).resetToCommitInDirectory(hash, path)
            else:
                if hash != Git.Git(path).currentCheckedOutCommit() or hard:
                    if hard:
                        Git.Git(path).resetHard()

                    Git.Git(path).checkoutCommit(hash)

        self.cur_checkouts[reponame] = hashes

        if prune:
            for name in os.listdir(self.checkout_root_path(reponame, None)):
                if name not in hashes.values():
                    print "pruning branch-checkout", self.repoShortname(reponame), name
                    self.clearDirectoryAsRoot(self.checkout_root_path(reponame, name))
                    shutil.rmtree(self.checkout_root_path(reponame, name))

    def commitsForRepo(self, reponame):
        if reponame not in self.cur_checkouts:
            raise UserWarning("Can't find " + reponame + " amongst checked out repos %s" % sorted(self.cur_checkouts))

        return self.cur_checkouts[reponame]

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
        commit, test = self.bestTest(repo, test)

        tests, environments, repos = self.resolver.testEnvironmentAndRepoDefinitionsFor(repo, commit)

        if test not in tests:
            raise UserWarning("Can't find test %s" % test)

        testDef = tests[test]

        print "test: ", test

        print "dependencies: "
        for depname, dep in sorted(testDef.dependencies.iteritems()):
            if dep.matches.InternalBuild:
                print "\tbuild: ", dep.name
            if dep.matches.ExternalBuild:
                print "\tbuild: ", self.repoShortname(dep.repo) + "/" + dep.commitHash + "/" + dep.name
            if dep.matches.Source:
                print "\tsource:", self.repoShortname(dep.repo) + "/" + dep.commitHash

    def infoForRepo(self, repo):
        repo = self.bestRepo(repo)

        print "repo: ", self.repoShortname(repo)

        git_repo = self.getGitRepo(repo)
        
        for branchname, sha_hash in git_repo.listBranchesForRemote("origin").iteritems():
            print "\t", branchname, " -> ", sha_hash

        for commit in self.commitsForRepo(repo):
            tests, environments, repos = self.resolver.fullyResolvedTestEnvironmentAndRepoDefinitionsFor(repo, commit)

            print self.cur_checkouts[repo][commit], " -> ", commit

            print "\tbuilds: "
            for test, testDef in tests.iteritems():
                if testDef.matches.Build:
                    print "\t\t", test

            print "\ttests: "
            for test, testDef in tests.iteritems():
                if testDef.matches.Test:
                    print "\t\t", test

            print "\trepos: "
            for repo, repoDef in repos.iteritems():
                if repoDef.matches.Pin:
                    print "\t\t", repo, "->", "/".join(repoDef.reference.split("/")[:-1] + [repoDef.branch]), "=", repoDef.commitHash()

            print "\trepo imports: "
            for repo, repoDef in repos.iteritems():
                if repoDef.matches.ImportedReference:
                    print "\t\t", repo, "from", repoDef.import_source, "=", repoDef.orig_reference, "=", repoDef.commitHash()

            print "\tenvironments: "
            for envName, envDef in environments.iteritems():
                print "\t\t", envName

    def bestRepo(self, reponame):
        if reponame in self.allRepoNames:
            return reponame
        
        for path in sorted(self.repo_prefixes_to_strip, key=len):
            if path + reponame in self.allRepoNames:
                return path + reponame

        for path in sorted(self.repo_prefixes_to_strip, key=len):
            if self.getGitRepo(path + reponame):
                return path + reponame

        return reponame


    def walkCheckedOutRepos(self, f):
        if self.checkout_root is None:
            return None

        seen = set()

        def walk(reponame, committish):
            if (reponame, committish) in seen:
                return
            seen.add((reponame, committish))

            if self.repoIsNotIgnored(reponame):
                f(reponame, committish)

            _,_,repos = self.resolver.fullyResolvedTestEnvironmentAndRepoDefinitionsFor(reponame, committish)

            for v in repos.values():
                walk(v.reponame(), v.commitHash())

        walk(self.checkout_root[0], self.checkout_root[1])

    def status(self, args):
        if self.checkout_root is None:
            print "Nothing checked out..."
            return

        def printer(reponame, committish):
            root = self.checkout_root_path(reponame, committish)
            git = Git.Git(root)
            print self.repoShortname(reponame), self.cur_checkouts[reponame][committish]
            diffstat = git.currentFileNumStat()
            for path in diffstat:
                print "\t++ %-5d  -- %-5d   %s" % (diffstat[path][0], diffstat[path][1], path)

        self.walkCheckedOutRepos(printer)
        

    def _pickOne(self, lookfor, possibilities, kindOfThing):
        possible = [item for item in possibilities if lookfor in item]
        if len(possible) == 1:
            return possible[0]
        if len(possible) > 1:
            #if it's an exact match, then use that.
            if lookfor in possible:
                return lookfor

            raise UserWarning("%s could refer to %s of %s" % (lookfor, "any" if len(possible) > 2 else "either", possible))

        return None

    def bestTest(self, reponame, test):
        possible = []

        for commit in self.cur_checkouts[reponame]:
            tests = self.resolver.testEnvironmentAndRepoDefinitionsFor(reponame, commit)[0]
            res = self._pickOne(test, tests, "test")
            if res:
                possible.append((commit, res))

        if not possible:
            raise UserWarning("Couldn't find a test named %s" % test)

        if len(possible) > 1:
            raise UserWarning(
                "Found multiple tests: " + 
                    ", ".join([self.cur_checkouts[reponame][commit] + "/" + test 
                            for commit,test in possible])
                )

        return possible[0]

    def build(self, args):
        repo = self.bestRepo(args.repo)

        commit, test = self.bestTest(repo, args.test)

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
            return True

        seen_already.add(path)

        all_tests = self.resolver.testEnvironmentAndRepoDefinitionsFor(reponame, commit)[0]

        if testname not in all_tests:
            raise UserWarning("Can't find test/build %s/%s/%s" % (reponame, commit, testname))

        testDef = all_tests[testname]

        if not nodeps:
            for depname, dep in testDef.dependencies.iteritems():
                if dep.matches.Source:
                    pass
                if dep.matches.InternalBuild:
                    if not self.buildTest(reponame, commit, dep.name, cores, nologcapture, nodeps, interactive, seen_already):
                        print "Dependent build ", self.repoShortname(reponame), commit, dep.name, " failed"
                        return False
                if dep.matches.ExternalBuild:
                    if not self.buildTest(dep.repo, dep.commitHash, dep.name, cores, nologcapture, nodeps, interactive, seen_already):
                        print "Dependent build ", self.repoShortname(dep.repo), dep.commitHash, dep.name, " failed"
                        return False
        
        print "Building ", self.repoShortname(reponame), commit, testname

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

        testDef = testDef._withReplacement(environment=self.resolver.resolveEnvironment(testDef.environment))

        if not worker_state.runTest("interactive", reponame, commit, testname, callbacks, testDef, interactive)[0]:
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

            ctl = TestLooperCtl(root)

            try:
                if parsedArgs.command == "checkout":
                    ctl.checkout(parsedArgs)
                elif parsedArgs.command == "clear":
                    ctl.clear(parsedArgs)
                elif parsedArgs.command == "info":
                    ctl.info(parsedArgs)
                elif parsedArgs.command == "build":
                    ctl.build(parsedArgs)
                elif parsedArgs.command == "fetch":
                    ctl.fetch(parsedArgs)
                elif parsedArgs.command == "status":
                    ctl.status(parsedArgs)
                elif parsedArgs.command == "cd":
                    ctl.change_directory(parsedArgs)
                elif parsedArgs.command == "branch_create":
                    ctl.create_branch(parsedArgs)
                else:
                    raise UserWarning("Unknown command " + parsedArgs.command)
            finally:
                ctl.writeState()

    except UserWarning as e:
        print "Error:\n\n%s" % str(e)
        #print traceback.format_exc()
        return 1    

    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
