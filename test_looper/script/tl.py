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

    @staticmethod
    def sanitizeName(name):
        return name.replace("_", "_u_").replace("/","_s_").replace("\\", "_bs_").replace(":","_c_").replace(" ","_sp_")

    def upload_build(self, testHash, key_name, file_name):
        pass

    def build_exists(self, testHash, key_name):
        pass

    def uploadSingleTestArtifact(self, testHash, testId, artifact_name, path):
        pass

    def uploadIndividualTestArtifacts(self, testHash, testId, pathsToUpload):
        pass

    def uploadTestArtifacts(self, *args, **kwargs):
        pass

class WorkerStateOverride(WorkerState.WorkerState):
    def __init__(self, name_prefix, worker_directory, looperCtl, cores):
        hwConfig = Config.HardwareConfig(cores=cores, ram_gb=8)

        image_repo = os.getenv("TESTLOOPER_DOCKER_IMAGE_REPO") or None

        WorkerState.WorkerState.__init__(self, name_prefix, worker_directory, None, DummyArtifactStorage(), "machine", hwConfig, docker_image_repo=image_repo)
        
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

    def grabDependency(self, log_function, expose_as, dep, worker_callback):
        target_dir = os.path.join(self.directories.test_inputs_dir, expose_as)

        if dep.matches.Build:
            self.extra_mappings[
                os.path.join(self.looperCtl.build_path(dep.buildHash), "build_output")
                ] = os.path.join("/test_looper", expose_as)

            return None

        if dep.matches.Source:
            self.extra_mappings[
                self.looperCtl.checkout_root_path(dep.repo, dep.commitHash)
                ] = os.path.join("/test_looper", expose_as)

            return None

        return "Unknown dependency type: %s" % dep


class TestDefinitionResolverOverride(TestDefinitionResolver.TestDefinitionResolver):
    def __init__(self, looperCtl):
        TestDefinitionResolver.TestDefinitionResolver.__init__(self, looperCtl.getGitRepo)
        self.looperCtl = looperCtl

    def getRepoContentsAtPath(self, repoName, commitHash, path):
        branchname = self.looperCtl.repo_and_hash_to_branch.get((repoName, commitHash))

        if branchname:
            root_path = self.looperCtl.checkout_root_path(repoName, branchname)
        else:
            root_path = self.looperCtl.checkout_root_path(repoName, commitHash)

        if os.path.exists(root_path):
            final_path = os.path.join(root_path, path)
            if not os.path.exists(final_path):
                return None
            else:
                return open(final_path, "r").read()

        git_repo = self.git_repo_lookup(repoName)
        return git_repo.getFileContents(commitHash, path)

    def testDefinitionTextAndExtensionFor(self, repoName, commitHash):
        branchname = self.looperCtl.repo_and_hash_to_branch.get((repoName, commitHash))

        if branchname:
            root_path = self.looperCtl.checkout_root_path(repoName, branchname)
        else:
            root_path = self.looperCtl.checkout_root_path(repoName, commitHash)

        if os.path.exists(root_path):
            #we have this checked out already, and want to use the local version of it
            path = Git.Git.getTestDefinitionsPathFromDir(root_path)

            if not path:
                return None

            text = open(os.path.join(root_path, path), "r").read()

            return text, os.path.splitext(path)[1], path

        repo = self.looperCtl.getGitRepo(repoName)

        if not repo.commitExists(commitHash):
            print "Can't find ", commitHash, " in ", repoName, ", so fetching origin..."
            repo.fetchOrigin()

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
                self.repo_and_hash_to_branch = {}
                self.checkout_root = (None, None)
                return
        
            with open(state_file_path, "r") as f:
                state = yaml.load(f.read())

            #map from (repo,hash) -> branchname
            #when we checkout a repo/commit or repo/branch, each commit is tied
            #to a branch, and we maintain that.
            self.repo_and_hash_to_branch = state.get("repo_and_hash_to_branch", {})

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

    def build_path(self, buildHash):
        return os.path.abspath(os.path.join(self.root_path, "builds", buildHash))

    def sanitizeReponame(self, reponame):
        return self.sanitize(reponame)

    def checkout_root_path(self, reponame, hashOrCommitName):
        if hashOrCommitName is None:
            return os.path.abspath(os.path.join(*((self.root_path, "src") + tuple(reponame.split("/")))))

        if Git.isShaHash(hashOrCommitName):
            hashOrCommitName = self.repo_and_hash_to_branch.get((reponame, hashOrCommitName), hashOrCommitName)

        return os.path.abspath(os.path.join(*((self.root_path, "src") + tuple(reponame.split("/")) + (self.sanitize(hashOrCommitName),))))
    
    def writeState(self):
        config = {
            "repo_and_hash_to_branch": self.repo_and_hash_to_branch,
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
            self.repo_and_hash_to_branch = {}
            self.clearDirectoryAsRoot(os.path.join(self.root_path, "src"))

        if args.build:
            self.clearDirectoryAsRoot(os.path.join(self.root_path, "builds"))

    def createNewBranchAndPush(self, repo, branchname, from_name):
        if from_name:
            hash = repo.gitCommitData("origin/" + from_name)[0]

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

        if not Git.isShaHash(committish) or not repo.commitExists(committish):
            #make sure we have up-to-date branch info
            repo.fetchOrigin()

        if args.from_name:
            self.createNewBranchAndPush(repo, committish, args.from_name)
        elif args.orphan:
            self.createNewBranchAndPush(repo, committish, None)

        if Git.isShaHash(committish):
            commitHash = committish
            branch = repo.closestBranchFor(committish)
        else:
            branches = repo.listCurrentlyKnownBranchesForRemote("origin")

            if committish in branches:
                branch = committish
                commitHash = repo.gitCommitData("origin/" + committish)[0]
            elif "^" in committish or "~" in committish:
                branch = committish.split("^")[0]
                branch = committish.split("~")[0]

                if branch not in branches:
                    raise UserWarning("Can't find branch %s in repo %s" % (branch, reponame))

                try:
                    commitHash = repo.gitCommitData("origin/" + committish)[0]
                except:
                    raise UserWarning("Invalid committish %s" % committish)
            else:
                raise UserWarning("Can't find branch %s in repo %s. Available branches:\n%s" % 
                        (committish, reponame, "\n".join(["    " + x for x in branches])))

        if branch not in repo.listCurrentlyKnownBranchesForRemote("origin"):
            print "couldn't identify a branch for %s in repo %s" % (commitHash, reponame)
            branch = None

        print "Checking out ", reponame, commitHash, " to branch ", branch

        self.checkout_root = (reponame, branch or commitHash)
        self.repo_and_hash_to_branch = {}

        repo_and_branch_to_commit = {}
        repo_and_naked_hash = set()

        def resolve(reponame, branchname, committish):
            #now find all the dependent repos and make sure we have them as well
            tests,envs,repos = self.resolver.testEnvironmentAndRepoDefinitionsFor(reponame, committish)

            for v in repos.values():
                import_repo_ref(v.reponame(), v.branchname(), v.commitHash())

        def import_repo_ref(reponame, branchname, commitHash):
            if branchname:
                if (reponame, branchname) not in repo_and_branch_to_commit:
                    repo_and_branch_to_commit[reponame, branchname] = commitHash
                else:
                    if repo_and_branch_to_commit[reponame, branchname] != commitHash:
                        print "Warning: %s/%s used with both %s and %s" % (
                            reponame, branchname, commitHash, repo_and_branch_to_commit[reponame, branchname]
                            )
                        #pick the closer
                        newHashDistance = repo.distanceForCommitInBranch(commitHash, branchname)
                        oldHashDistance = repo.distanceForCommitInBranch(repo_and_branch_to_commit[reponame, branchname], branchname)
                        if newHashDistance < oldHashDistance:
                            repo_and_naked_hash.add((reponame, repo_and_branch_to_commit[reponame, branchname]))
                            repo_and_branch_to_commit[reponame,branchname] = commitHash
                        else:
                            repo_and_naked_hash.add((reponame, commitHash))
            else:
                repo_and_naked_hash.add((reponame, commitHash))

            resolve(reponame, branchname, commitHash)

        import_repo_ref(reponame, branch, commitHash)

        for (reponame, branchname), commitHash in repo_and_branch_to_commit.iteritems():
            self.repo_and_hash_to_branch[(reponame, commitHash)] = branchname

        for reponame, branchname in sorted(repo_and_branch_to_commit):
            self._checkoutRepoName(reponame, branchname, repo_and_branch_to_commit[reponame, branchname], args.hard)

        for reponame, hash in repo_and_naked_hash:
            self._checkoutRepoName(reponame, None, hash, args.hard)

        allRepos = set([x[0] for x in repo_and_branch_to_commit]+ [x[0] for x in repo_and_naked_hash])

        if args.prune:
            for reponame in self.allRepoNames:
                rootPath = self.checkout_root_path(reponame, None)
                if reponame not in allRepos:
                    if os.path.exists(rootPath):
                        print "pruning entire repo reference ", self.repoShortname(reponame)
                        self.clearDirectoryAsRoot(rootPath)
                        shutil.rmtree(rootPath)
                else:
                    if os.path.exists(rootPath):
                        for item in os.listdir(rootPath):
                            if (reponame, item) not in repo_and_branch_to_commit and (reponame, item) not in repo_and_naked_hash:
                                if os.path.exists(os.path.join(reponame, item)):
                                    print "pruning checkout ", self.repoShortname(reponame), item

                                    self.clearDirectoryAsRoot(os.path.join(reponame, item))
                                    shutil.rmtree(os.path.join(reponame, item))

        for reponame, branchname in sorted(repo_and_branch_to_commit):
            commit = repo_and_branch_to_commit[reponame, branchname]

            _,_,repos = self.resolver.testEnvironmentAndRepoDefinitionsFor(reponame, commit)

            if repos:
                print "%s/%s (%s)" % (self.repoShortname(reponame), branchname, commit)

                for refname, ref in repos.iteritems():
                    if self.repoIsNotIgnored(ref.reponame()):
                        print "\t", refname, " -> ", self.repoShortname(ref.reponame()), ref.branchname() or commit

    def _checkoutRepoName(self, reponame, branch, hash, hard=False):
        path = self.checkout_root_path(reponame, branch or hash)

        if not Git.Git(path).isInitialized():
            self.getGitRepo(reponame).resetToCommitInDirectory(hash, path)
        else:
            if hash != Git.Git(path).currentCheckedOutCommit() or hard:
                if hard:
                    Git.Git(path).resetHard()

                print "Checkout commit ", hash, " to ", path

                Git.Git(path).checkoutCommit(hash)

    def branchesCheckedOutForRepo(self, reponame):
        res = []

        for rn, hash in self.repo_and_hash_to_branch:
            if rn == reponame:
                res.append(self.repo_and_hash_to_branch[rn,hash])
        
        return res
        
    def info(self, args):
        if args.repo:
            if args.test:
                self.infoForTest(args.repo, args.test)
            else:
                self.infoForRepo(args.repo)
            return

        if self.checkout_root and self.checkout_root[0]:
            self.infoForRepo(self.checkout_root[0])
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

    def infoForRepo(self, reponame):
        reponame = self.bestRepo(reponame)

        print "repo: ", self.repoShortname(reponame)

        git_repo = self.getGitRepo(reponame)
        
        for branchname, sha_hash in git_repo.listBranchesForRemote("origin").iteritems():
            print "\t", branchname, " -> ", sha_hash

        for branchname in self.branchesCheckedOutForRepo(reponame):
            print reponame, branchname
            tests, environments, repos = self.resolver.testEnvironmentAndRepoDefinitionsFor(reponame, branchname)

            print branchname

            print "\tbuilds: "
            for test, testDef in sorted(tests.iteritems()):
                if testDef.matches.Build:
                    print "\t\t", test

            print "\ttests: "
            for test, testDef in sorted(tests.iteritems()):
                if testDef.matches.Test:
                    print "\t\t", test

            print "\trepos: "
            for repo, repoDef in sorted(repos.iteritems()):
                if repoDef.matches.Pin:
                    print "\t\t", repo, "->", "/".join(repoDef.reference.split("/")[:-1] + [repoDef.branch]), "=", repoDef.commitHash()

            print "\trepo imports: "
            for repo, repoDef in sorted(repos.iteritems()):
                if repoDef.matches.ImportedReference:
                    print "\t\t", repo, "from", repoDef.import_source, "=", repoDef.orig_reference, "=", repoDef.commitHash()

            print "\tenvironments: "
            for envName, envDef in sorted(environments.iteritems()):
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

            _,_,repos = self.resolver.testEnvironmentAndRepoDefinitionsFor(reponame, committish)

            for v in repos.values():
                walk(v.reponame(), v.commitHash())

        walk(self.checkout_root[0], self.checkout_root[1])

        for reponame, committish in sorted(seen):
            if self.repoIsNotIgnored(reponame):
                f(reponame, committish)

    def status(self, args):
        if self.checkout_root is None:
            print "Nothing checked out..."
            return

        def printer(reponame, committish):
            root = self.checkout_root_path(reponame, committish)
            git = Git.Git(root)
            print self.repoShortname(reponame), self.repo_and_hash_to_branch.get((reponame, committish), committish[:10] if Git.isShaHash(committish) else committish)
            
            if git.isInitialized():
                diffstat = git.currentFileNumStat()
                for path in diffstat:
                    print "\t++ %-5d  -- %-5d   %s" % (diffstat[path][0], diffstat[path][1], path)
            else:
                print "\tNOT INITIALIZED"

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

    def findTest(self, reponame, testHash):
        possible = []

        for commit in self.branchesCheckedOutForRepo(reponame):
            tests = self.resolver.testEnvironmentAndRepoDefinitionsFor(reponame, commit)[0]

            for test in tests.values():
                if test.hash == testHash:
                    return commit, test

    def bestTest(self, reponame, test):
        possible = []

        for commit in self.branchesCheckedOutForRepo(reponame):
            tests = self.resolver.testEnvironmentAndRepoDefinitionsFor(reponame, commit)[0]
            res = self._pickOne(test, tests, "test")
            if res:
                possible.append((commit, res))

        if not possible:
            raise UserWarning("Couldn't find a test named %s" % test)

        if len(possible) > 1:
            raise UserWarning(
                "Found multiple tests: " + 
                    ", ".join([self.repo_and_hash_to_branch[reponame,commit] + "/" + test 
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

        all_tests = self.resolver.testEnvironmentAndRepoDefinitionsFor(reponame, commit)[0]

        if testname not in all_tests:
            raise UserWarning("Can't find test/build %s/%s/%s" % (reponame, commit, testname))

        testDef = all_tests[testname]

        path = self.build_path(testDef.hash)

        if path in seen_already:
            return True

        seen_already.add(path)

        if not nodeps:
            for depname, dep in testDef.dependencies.iteritems():
                if dep.matches.Source:
                    pass
                if dep.matches.Build:
                    commit, test = self.findTest(dep.repo, dep.buildHash)

                    if not self.buildTest(dep.repo, commit, dep.name, cores, nologcapture, nodeps, interactive, seen_already):
                        print "Dependent build ", self.repoShortname(dep.repo), dep.name, " failed"
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

        if not worker_state.runTest("interactive", callbacks, testDef, interactive)[0]:
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
