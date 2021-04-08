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
import fnmatch
import subprocess
import yaml

##############
# dependencies
# pip install pyyaml
# pip install json
# pip install requests
# pip install psutil
#
# windows also:
# pip install pypiwin32

proj_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.append(proj_root)

import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.data_model.TestDefinitionResolver as TestDefinitionResolver
import test_looper.core.tools.Git as Git
import test_looper.core.Config as Config
import test_looper.worker.WorkerState as WorkerState
import test_looper.core.SubprocessRunner as SubprocessRunner

if sys.platform != "win32":
    import test_looper.core.tools.Docker as Docker
else:
    Docker = None
    import win32file

    FILE_ATTRIBUTE_REPARSE_POINT = 1024
    # To make things easier.
    REPARSE_FOLDER = win32file.FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT


if os.getenv("TESTLOOPER_AWS_CREDS"):
    try:
        with open(os.getenv("TESTLOOPER_AWS_CREDS"), "r") as f:
            creds = json.loads(f.read())

            os.environ["AWS_ACCESS_KEY_ID"] = str(creds["access_key_id"])
            os.environ["AWS_SECRET_ACCESS_KEY"] = str(creds["secret_access_key"])
            os.environ["AWS_SESSION_TOKEN"] = str(creds["session_token"])
    except:
        print(
            "WARNING: couldn't read credentials from ",
            os.getenv("TESTLOOPER_AWS_CREDS"),
        )

ROOT_CHECKOUT_NAME = "__root"


def printGrid(grid):
    grid = [[str(cell) for cell in row] for row in grid]

    col_count = max([len(row) for row in grid])
    gridWidths = []
    for col in range(col_count):
        gridWidths.append(
            max(
                [
                    len(grid[row][col]) if col < len(grid[row]) else 0
                    for row in range(len(grid))
                ]
            )
        )

    grid = grid[:1] + [["-" * g for g in gridWidths]] + grid[1:]

    rows = []
    for row in grid:
        fmt = "  ".join(["%-" + str(gridWidths[col]) + "s" for col in range(len(row))])
        rows.append(fmt % tuple(row))

    print("\n".join(rows) + "\n")


def configureLogging(verbose=False):
    loglevel = logging.INFO if verbose else logging.WARN
    logging.getLogger().setLevel(loglevel)

    for handler in logging.getLogger().handlers:
        handler.setLevel(loglevel)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(filename)s:%(lineno)s@%(funcName)s %(name)s - %(message)s"
            )
        )


def createArgumentParser():
    parser = argparse.ArgumentParser(
        description="Checkout multi-repo projects locally and run tests using docker"
    )

    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        default=False,
        action="store_true",
        help="Set logging level to verbose",
    )

    subparsers = parser.add_subparsers()

    init_parser = subparsers.add_parser("init", help="Initialize a testlooper checkout")
    init_parser.set_defaults(command="init")
    init_parser.add_argument("path", help="Path to disk storage")
    init_parser.add_argument(
        "git_clone_root", help="Git clone root (e.g. git@gitlab.mycompany.com)"
    )
    init_parser.add_argument(
        "-i",
        "--ignore",
        help="Any repos with these strings at the start are by convention not displayed in outputs",
        nargs="*",
    )
    init_parser.add_argument(
        "-s",
        "--strip",
        help="These strings are by default stripped from the front of reponames",
        nargs="*",
    )
    init_parser.add_argument(
        "--repos",
        help="store source repos somewhere other than '.tl/repos'",
        default=None,
    )

    fetch_parser = subparsers.add_parser(
        "fetch", help="Run 'git fetch origin' on all the repos we know about"
    )
    fetch_parser.set_defaults(command="fetch")
    fetch_parser.add_argument(
        "--all",
        help="Run fetch on all repos (even hidden ones)",
        default=False,
        action="store_true",
    )

    status_parser = subparsers.add_parser(
        "status", help="Show currently referenced repos and changed files."
    )
    status_parser.set_defaults(command="status")
    status_parser.add_argument(
        "--all",
        help="Show all repos (even hidden ones)",
        default=False,
        action="store_true",
    )

    checkout_parser = subparsers.add_parser(
        "checkout",
        help="Checkout a given repo/commit into 'src/src' and grab dependencies",
    )
    checkout_parser.set_defaults(command="checkout")
    checkout_parser.add_argument("repo", help="Name of the repo")
    checkout_parser.add_argument("committish", help="Name of the commit or branch")
    checkout_parser.add_argument(
        "--hard",
        help="Force a hard reset in the source repo (and all dependent source repos)",
        default=False,
        action="store_true",
    )
    checkout_parser.add_argument(
        "--prune", help="Get rid of unused repos", default=False, action="store_true"
    )
    checkout_parser.add_argument(
        "--from",
        help="Create a new branch, based on this one",
        dest="from_name",
        default=None,
    )
    checkout_parser.add_argument(
        "--orphan",
        help="Create a new orphaned branch, based on this one",
        dest="orphan",
        default=False,
        action="store_true",
    )

    run_parser = subparsers.add_parser("run", help="Run a build or test.")
    run_parser.set_defaults(command="run")
    run_parser.add_argument("testpattern", help="Name of the test (with globs)")

    if sys.platform != "win32":
        # no reason to have 'interactive' on windows - we're already 'interactive because there's no
        # docker involved
        run_parser.add_argument(
            "-i",
            "--interactive",
            dest="interactive",
            default=False,
            help="Drop into an interactive terminal for this.",
            action="store_true",
        )
    else:
        run_parser.set_defaults(interactive=False)

    run_parser.add_argument(
        "-c",
        dest="cmd",
        default=None,
        help="Just run this one command, instead of the full build",
    )
    run_parser.add_argument(
        "-d",
        "--nodeps",
        dest="nodeps",
        default=False,
        help="Don't build dependencies, just this one. ",
        action="store_true",
    )
    run_parser.add_argument(
        "-s",
        "--nologcapture",
        dest="nologcapture",
        default=False,
        help="Don't capture logs - show everything directly",
        action="store_true",
    )
    run_parser.add_argument(
        "-v",
        "--volume",
        help="Extra volumes to expose during the run. Pattern is 'host_path:docker_path'",
        dest="volumesToExpose",
        default=[],
        nargs="*",
    )
    run_parser.add_argument(
        "-j",
        "--cores",
        dest="cores",
        default=1,
        type=int,
        help="Number of cores to expose",
    )

    info_parser = subparsers.add_parser(
        "info", help="Get info on a particular test or group of tests."
    )
    info_parser.set_defaults(command="info")
    info_parser.add_argument(
        "testpattern",
        help="Subset of tests to look at in particular",
        default=[],
        nargs="*",
    )
    info_parser.add_argument(
        "-d",
        "--detail",
        help="Dump full test detail, not just the name",
        default=False,
        action="store_true",
    )
    info_parser.add_argument(
        "--all",
        help="Show all repos (even hidden ones) when displaying dependencies",
        default=False,
        action="store_true",
    )

    return parser


def loadConfiguration(configFile):
    with open(configFile, "r") as fin:
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
    root = os.path.abspath(args.path)

    curRoot = find_cur_root(root)
    if curRoot:
        raise UserWarning(
            "Can't initialize a tl directory here. There is already one at\n\n%s"
            % curRoot
        )

    if not os.path.exists(root):
        os.makedirs(root)

    os.mkdir(os.path.join(root, ".tl"))

    config_file = os.path.join(root, ".tl", "config.yml")
    with open(config_file, "w") as f:
        f.write(
            yaml.dump(
                {
                    "git_clone_root": args.git_clone_root,
                    "repo_path_override": os.path.abspath(args.repos),
                    "repo_prefixes_to_ignore": args.ignore,
                    "repo_prefixes_to_strip": args.strip,
                },
                indent=4,
                default_style="",
            )
        )


class DummyArtifactStorage(object):
    def __init__(self):
        object.__init__(self)

    @staticmethod
    def sanitizeName(name):
        return (
            name.replace("_", "_u_")
            .replace("/", "_s_")
            .replace("\\", "_bs_")
            .replace(":", "_c_")
            .replace(" ", "_sp_")
        )

    def upload_build(self, testHash, key_name, file_name):
        pass

    def build_exists(self, testHash, key_name):
        pass

    def uploadSingleTestArtifact(self, testHash, testId, artifact_name, path):
        pass

    def uploadIndividualTestArtifacts(
        self, testHash, testId, pathsToUpload, logger=None
    ):
        pass


class WorkerStateOverride(WorkerState.WorkerState):
    def __init__(
        self, name_prefix, worker_directory, looperCtl, cores, volumesToExpose
    ):
        hwConfig = Config.HardwareConfig(cores=cores, ram_gb=8)

        image_repo = os.getenv("TESTLOOPER_DOCKER_IMAGE_REPO") or None

        WorkerState.WorkerState.__init__(
            self,
            name_prefix,
            worker_directory,
            DummyArtifactStorage(),
            "machine",
            hwConfig,
            docker_image_repo=image_repo,
        )

        self.looperCtl = looperCtl
        self.extra_mappings = {}

        for extra in volumesToExpose:
            host_path, image_path = extra.split(":", 1)
            self.extra_mappings[host_path] = image_path

        self.resolver = TestDefinitionResolverOverride(looperCtl, None)

    def wants_to_run_cleanup(self):
        return False

    def getRepoCacheByName(self, name):
        return self.looperCtl.getGitRepo(name)

    def resetToCommitInDir(self, repoName, commitHash, targetDir):
        assert False

    def cleanup(self):
        if Docker is not None:
            Docker.DockerImage.removeDanglingDockerImages()

            # don't remove everything!
            self.clearDirectoryAsRoot(
                self.directories.scratch_dir,
                # self.directories.test_inputs_dir,
                self.directories.command_dir,
            )
        else:
            self.clearDirectoryAsRoot(self.directories.command_dir)

    def volumesToExpose(self):
        res = {
            self.directories.scratch_dir: "/test_looper/scratch",
            self.directories.test_output_dir: "/test_looper/output",
            self.directories.build_output_dir: "/test_looper/build_output",
            self.directories.test_inputs_dir: "/test_looper/test_inputs",
            self.directories.ccache_dir: "/test_looper/ccache",
            self.directories.command_dir: "/test_looper/command",
        }

        res.update(self.extra_mappings)

        return res

    def _upload_artifact(self, *args, **kwargs):
        return True

    def resetToCommit(self, repoName, commitHash):
        self.extra_mappings[
            self.looperCtl.checkout_root_path(repoName)
        ] = self.exposeAsDir("src")

        return True

    def exposeAsDir(self, expose_as):
        if sys.platform == "win32":
            return os.path.join(self.worker_directory, expose_as)
        else:
            assert expose_as.startswith("test_inputs/")
            tgt = os.path.join(
                "/test_looper/mountpoints", expose_as[len("test_inputs/") :]
            )

            target_linkpoint = os.path.join(
                self.directories.test_inputs_dir, expose_as[len("test_inputs/") :]
            )
            if not os.path.exists(os.path.dirname(target_linkpoint)):
                os.makedirs(os.path.dirname(target_linkpoint))

            if os.path.islink(target_linkpoint):
                os.unlink(target_linkpoint)

            os.symlink(tgt, target_linkpoint)
            return tgt

    def grabDependency(self, log_function, expose_as, dep, worker_callback):
        if dep.matches.Build:
            self.extra_mappings[
                os.path.join(self.looperCtl.build_path(dep.name), "build_output")
            ] = self.exposeAsDir(expose_as)

            return None

        if dep.matches.Source:
            subpath = self.looperCtl.checkout_root_path(dep.repo)

            if dep.path:
                subpath = os.path.join(subpath, dep.path)

            self.extra_mappings[subpath] = self.exposeAsDir(expose_as)

            return None

        return "Unknown dependency type: %s" % dep

    def _windows_prerun_command(self):
        def islink(fpath):
            """ Windows islink implementation. """
            if win32file.GetFileAttributes(fpath) & REPARSE_FOLDER == REPARSE_FOLDER:
                return True
            return False

        def walkToSymlinks(dir):
            if islink(dir):
                os.rmdir(dir)
            else:
                for p in os.listdir(dir):
                    if not os.path.isdir(os.path.join(dir, p)):
                        os.unlink(os.path.join(dir, p))
                    else:
                        walkToSymlinks(os.path.join(dir, p))
                os.rmdir(dir)

        walkToSymlinks(self.directories.test_inputs_dir)

        if os.path.exists(self.directories.repo_copy_dir):
            os.rmdir(self.directories.repo_copy_dir)

        for k, v in self.extra_mappings.items():
            if not os.path.exists(os.path.dirname(v)):
                os.makedirs(os.path.dirname(v))

            args = ["New-Item", "-Path", v, "-ItemType", "Junction", "-Value", k]

            running_subprocess = subprocess.Popen(
                ["powershell.exe", "-ExecutionPolicy", "Bypass"] + args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
                # env=env_to_pass,
                # creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            running_subprocess.wait()


class TestDefinitionResolverOverride(TestDefinitionResolver.TestDefinitionResolver):
    def __init__(self, looperCtl, visitRepoRef):
        TestDefinitionResolver.TestDefinitionResolver.__init__(
            self, looperCtl.getGitRepo
        )
        self.looperCtl = looperCtl
        self.visitRepoRef = visitRepoRef

    def resolveRefWithinRepo(self, curRepoName, curCommitHash, nameOfRef, actualRef):
        """
        Allows subclasses to modify how we name repositories. 

        curRepoName - the name of the repo we're currently parsing
        nameOfRef - the name of the reference within the testDefinitions file
        actualRef - the RepoReference (not an Import) we're processing.
        """
        path = ":".join(curRepoName.split(":")[:-1] + [nameOfRef])

        if actualRef.reference == "HEAD":
            actualRepoName = curRepoName.split(":")[-1]
            actualHash = curCommitHash
        else:
            actualRepoName = actualRef.reponame().split(":")[-1]
            actualHash = actualRef.commitHash()

        res = actualRef._withReplacement(
            reference=path + ":" + actualRepoName + "/" + actualHash
        )

        if self.visitRepoRef:
            self.visitRepoRef(res)

        return res

    def mostRecentHashForSubpath(self, repoName, commitHash, path):
        return commitHash

    def getRepoContentsAtPath(self, repoName, commitHash, path):
        root_path = self.looperCtl.checkout_root_path(repoName)

        if os.path.exists(root_path):
            final_path = os.path.join(root_path, path)
            if not os.path.exists(final_path):
                return None
            else:
                return open(final_path, "r").read()

        return None

    def testDefinitionTextAndExtensionFor(self, repoName, commitHash):
        root_path = self.looperCtl.checkout_root_path(repoName)

        if os.path.exists(root_path):
            # we have this checked out already, and want to use the local version of it
            path = Git.Git.getTestDefinitionsPathFromDir(root_path)

            if not path:
                return None

            text = open(os.path.join(root_path, path), "r").read()

            return text, os.path.splitext(path)[1], path

        return None


class TestLooperCtl:
    def __init__(self, root_path):
        self.root_path = root_path
        self.repos = {}
        self.path_to_repos = None

        self._loadConfig()
        self._loadState()

        self.initializeAllRepoNames()

    def _loadState(self):
        try:
            state_file_path = os.path.join(self.root_path, ".tl", "state.yml")

            if not os.path.exists(state_file_path):
                self.checkout_root = (None, None)
                return

            with open(state_file_path, "r") as f:
                state = yaml.load(f.read())

            # repo, branch (or None), commit
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
            self.path_to_repos = config.get("repo_path_override", None) or os.path.join(
                self.root_path, ".tl", "repos"
            )

            self.git_clone_root = config["git_clone_root"]

        except Exception as e:
            raise UserWarning("Corrupt config file: " + str(e))

    def repoShouldBeDisplayed(self, repo):
        repo = repo.split(":")[-1]

        for r in self.repo_prefixes_to_ignore:
            if repo.startswith(r):
                return False
        return True

    def repoShortname(self, repo):
        if repo == ROOT_CHECKOUT_NAME:
            return ""

        prunes = [p for p in self.repo_prefixes_to_strip if repo.startswith(p)]
        prunes = sorted(prunes, key=len)

        if prunes:
            return repo[len(prunes[-1]) :]
        return repo

    def initializeAllRepoNames(self):
        self.allRepoNames = set()

        def walk(items):
            dirpath = os.path.join(self.path_to_repos, *items)

            if os.path.exists(dirpath):
                for i in os.listdir(dirpath):
                    fullpath = os.path.join(dirpath, i)
                    if i != ".git" and os.path.isdir(fullpath):
                        if os.path.exists(os.path.join(fullpath, ".git")):
                            self.allRepoNames.add("/".join(items + (i,)))
                        else:
                            walk(items + (i,))

        walk(())

    def fetch(self, args):
        threads = []
        for reponame in self.allRepoNames:
            if args.all or self.repoShouldBeDisplayed(reponame):

                def makeUpdater(name):
                    def f():
                        try:
                            self.getGitRepo(name).fetchOrigin()
                        except:
                            logging.error(
                                "Failed to update repo %s: %s",
                                name,
                                traceback.format_exc(),
                            )

                    return f

                threads.append(threading.Thread(target=makeUpdater(reponame)))
                threads[-1].daemon = True
                threads[-1].start()

        print("fetching origin for ", len(threads), " repos...")

        for t in threads:
            t.join()

    def sanitize(self, name):
        return name.replace("/", "_").replace(":", "_").replace("~", "--")

    def build_path(self, buildname):
        return os.path.abspath(
            os.path.join(self.root_path, "builds", self.sanitize(buildname))
        )

    def checkout_root_path(self, reponame):
        """Return the checkout location of a given repo. 

        The reponame is actually an encoding of the reponame and the path (of 
        repo-variables) that lead us here from the root checkout. That is,
        if the root test definition refers to hash "H1" in repo "MyRepo" as "my_repo_src",
        the reponame will be encoded as

            my_repo_src:MyRepo

        if my_repo_src ends up referring to MySubRepo, that would be encoded as 

            my_repo_src:my_subrepo_src:MySubRepo

        The root commit is always encoded as ROOT_CHECKOUT_NAME and mapped to 'src'.
        """
        if reponame.split(":")[-1] == ROOT_CHECKOUT_NAME:
            return os.path.join(self.root_path, "src", "src")

        path = ".".join(reponame.split(":")[:-1])

        if not self.repoShouldBeDisplayed(reponame):
            return os.path.join(self.root_path, "hidden", path)

        return os.path.join(self.root_path, "src", path)

    def writeState(self):
        config = {"checkout_root": self.checkout_root}

        with open(os.path.join(self.root_path, ".tl", "state.yml.tmp"), "w") as f:
            f.write(yaml.dump(config, indent=4, default_style=""))
        os.rename(
            os.path.join(self.root_path, ".tl", "state.yml.tmp"),
            os.path.join(self.root_path, ".tl", "state.yml"),
        )

    def getGitRepo(self, reponame):
        if reponame in self.repos:
            return self.repos[reponame]

        self.repos[reponame] = Git.Git(
            os.path.join(*([self.path_to_repos] + reponame.split("/")))
        )
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
                print(
                    "Cloned "
                    + clone_root
                    + " into "
                    + self.repos[reponame].path_to_repo
                )

        return self.repos[reponame]

    def clearDirectoryAsRoot(self, *args):
        image = Docker.DockerImage("ubuntu:16.04")
        image.run(
            "rm -rf " + " ".join(["%s/*" % p for p in args]),
            volumes={a: a for a in args},
            options="--rm",
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

    def setCurCheckoutRoot(self, args):
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
            # make sure we have up-to-date branch info
            repo.fetchOrigin()

        if args.from_name:
            self.createNewBranchAndPush(repo, committish, args.from_name)
        elif args.orphan:
            self.createNewBranchAndPush(repo, committish, None)

        if Git.isShaHash(committish):
            commitHash = committish
        else:
            commitHash = repo.gitCommitData("origin/" + committish)[0]

        print("Checking out", reponame, commitHash)

        self.checkout_root = (reponame, commitHash)

    def checkout(self, args):
        self.setCurCheckoutRoot(args)

        self.getGitRepo(self.checkout_root[0]).resetToCommitInDirectory

        # make sure our root checkout (src/src) is populated
        self._updateRepoCheckout(ROOT_CHECKOUT_NAME, self.checkout_root[1], args.hard)

        paths_visited = set()

        def visitRepoRef(ref):
            if (
                ref.reponame().split(":")[-1] == ROOT_CHECKOUT_NAME
                and ref.commitHash() == "HEAD"
            ):
                return

            path = self.checkout_root_path(ref.reponame())

            if path in paths_visited:
                return
            else:
                paths_visited.add(path)

            self._updateRepoCheckout(ref.reponame(), ref.commitHash(), args.hard)

        # walk all the repo definitions and make sure everything is up-to-date
        resolver = TestDefinitionResolverOverride(self, visitRepoRef)
        resolver.testEnvironmentAndRepoDefinitionsFor(ROOT_CHECKOUT_NAME, "HEAD")

        if args.prune:
            all_paths = self.allCheckoutPaths()
            for path in all_paths:
                if path not in paths_visited:
                    print("Removing", path)
                    try:
                        shutil.rmtree(path)
                    except:
                        traceback.print_exc()

    def allCheckoutPaths(self):
        res = []

        for dirname in os.listdir(os.path.join(self.root_path, "src")):
            if dirname != "src":
                res.append(os.path.join(self.root_path, "src", dirname))

        for dirname in os.listdir(os.path.join(self.root_path, "hidden")):
            res.append(os.path.join(self.root_path, "hidden", dirname))

        return res

    def _updateRepoCheckout(self, reponame, hash, hard=False):
        if reponame == ROOT_CHECKOUT_NAME:
            actualRepoName = self.checkout_root[0]
        else:
            actualRepoName = reponame.split(":")[-1]

        path = self.checkout_root_path(reponame)
        repo = Git.Git(path)

        if not repo.isInitialized():
            print("Checking out ", hash, " from ", actualRepoName, " to ", path)
            self.getGitRepo(actualRepoName).resetToCommitInDirectory(hash, path)
        else:
            if repo.getSourceRepoName("origin") != actualRepoName:
                if repo.currentFileNumStat():
                    print(
                        "Repo reference for ",
                        "/".join(reponame.split(":")[:-1]),
                        "changed from ",
                        repo.getSourceRepoName("origin"),
                        "to",
                        actualRepoName,
                    )
                    print()
                    print(
                        "You have outstanding changes. Please remove them before continuing."
                    )
                    os._exit(1)
                else:
                    print(
                        "Repo reference for ",
                        "/".join(reponame.split(":")[:-1]),
                        "changed from ",
                        repo.getSourceRepoName("origin"),
                        "to",
                        actualRepoName,
                    )
                    print("No files are modified so we're replacing the directory.")
                    shutil.rmtree(path)
                    self.getGitRepo(actualRepoName).resetToCommitInDirectory(hash, path)

            if hash != repo.currentCheckedOutCommit() or hard:
                if hard:
                    repo.resetHard()

                print(
                    "Checkout commit ",
                    hash,
                    " to ",
                    path,
                    " currently at ",
                    Git.Git(path).currentCheckedOutCommit(),
                )

                repo.checkoutCommit(hash)

                if repo.currentCheckedOutCommit() != hash:
                    print("Fetching origin for ", path)
                    repo.fetchOrigin()
                    repo.checkoutCommit(hash)

                if repo.currentCheckedOutCommit() != hash:
                    print("Failed to checkout ", hash, " into ", path)
                    if repo.currentFileNumStat():
                        print(
                            "You have outstanding changes that are conflicting with the checkout."
                        )

                    os._exit(1)

    def allTestsAndBuildsByName(self):
        visited = set()

        def visitRepoRef(ref):
            path = self.checkout_root_path(ref.reponame())

            if path in visited:
                return
            else:
                visited.add(path)

            repo = Git.Git(path)
            if not repo.isInitialized():
                self._updateRepoCheckout(ref.reponame(), ref.commitHash())

        resolver = TestDefinitionResolverOverride(self, visitRepoRef)
        resolver.testEnvironmentAndRepoDefinitionsFor(ROOT_CHECKOUT_NAME, "HEAD")

        allTestsByName = {}

        for (repo, hash), testDict in resolver.testDefinitionCache.items():
            for testName, testDefinition in testDict.items():
                if repo == ROOT_CHECKOUT_NAME:
                    allTestsByName[testName] = (testDefinition, repo)
                else:
                    repoName = "/".join(repo.split(":")[:-1])

                    allTestsByName[repoName + "/" + testName] = (testDefinition, repo)

        return allTestsByName

    def artifactsInTestDef(self, testDef):
        return [a.name for stage in testDef.stages for a in stage.artifacts]

    def run(self, args):
        if args.interactive:
            if not args.nodeps:
                print("Interactive implies no dependencies.")
            if not args.nologcapture:
                print("Interactive implies nologcapture")
            args.nologcapture = True
            args.nodeps = True

        if args.cmd:
            args.nodeps = True
            args.nologcapture = True

        all_tests = self.allTestsAndBuildsByName()

        possible_tests = {
            t: all_tests[t]
            for t in all_tests
            if fnmatch.fnmatchcase(t, args.testpattern)
        }

        if not possible_tests:
            print(
                "Can't find a test or build matching pattern '%s' amongst "
                % args.testpattern
            )
            for test in sorted(all_tests):
                print("    " + test)
        else:
            if args.cmd and len(possible_tests) != 1:
                raise UserWarning("Explicit commands can only be passed to one target.")

            buildToArtifactsNeeded = {}
            for test in sorted(possible_tests):
                testDef = possible_tests[test][0]

                if testDef.matches.Build:
                    buildToArtifactsNeeded[testDef.name] = self.artifactsInTestDef(
                        testDef
                    )

            for test in sorted(possible_tests):
                self.walkGraphAndFillOutTestArtifacts(
                    all_tests, possible_tests[test][0], buildToArtifactsNeeded
                )

            for test in sorted(possible_tests):
                self.runBuildOrTest(
                    all_tests,
                    possible_tests[test][1],
                    possible_tests[test][0],
                    args.cores,
                    args.nologcapture,
                    args.nodeps,
                    args.interactive,
                    set(),
                    args.cmd,
                    buildToArtifactsNeeded,
                    args.volumesToExpose,
                )

    def walkGraphAndFillOutTestArtifacts(
        self, all_tests, testDef, buildToArtifactsNeeded, seen_already=None
    ):
        """Walk all the dependent tests needed by 'testDef' and get a list of the artifacts we really need to build."""
        if not seen_already:
            seen_already = set()

        path = self.build_path(testDef.name)

        if path in seen_already:
            return

        seen_already.add(path)

        for depname, dep in testDef.dependencies.items():
            if dep.matches.Build:
                test_and_repo = None

                for t in all_tests:
                    if all_tests[t][0].hash == dep.buildHash:
                        test_and_repo = all_tests[t]

                if test_and_repo:
                    subdef, subrepo = test_and_repo

                    if subdef.name not in buildToArtifactsNeeded:
                        buildToArtifactsNeeded[subdef.name] = []

                    if dep.artifact not in buildToArtifactsNeeded[subdef.name]:
                        buildToArtifactsNeeded[subdef.name].append(dep.artifact)

                    self.walkGraphAndFillOutTestArtifacts(
                        all_tests, subdef, buildToArtifactsNeeded, seen_already
                    )

    def runBuildOrTest(
        self,
        all_tests,
        reponame,
        testDef,
        cores,
        nologcapture,
        nodeps,
        interactive,
        seen_already,
        explicit_cmd=None,
        artifactSubsetByBuildName=None,
        volumesToExpose=[],
    ):
        # walk all the repo definitions and make sure everything is up-to-date

        path = self.build_path(testDef.name)

        if path in seen_already:
            return True

        seen_already.add(path)

        if not nodeps:
            for depname, dep in testDef.dependencies.items():
                if dep.matches.Build:
                    test_and_repo = None

                    for t in all_tests:
                        if all_tests[t][0].hash == dep.buildHash:
                            test_and_repo = all_tests[t]

                    if test_and_repo:
                        subdef, subrepo = test_and_repo
                        if not self.runBuildOrTest(
                            all_tests,
                            subrepo,
                            subdef,
                            cores,
                            nologcapture,
                            nodeps,
                            interactive,
                            seen_already,
                            artifactSubsetByBuildName=artifactSubsetByBuildName,
                            volumesToExpose=volumesToExpose,
                        ):
                            print(
                                "Dependent build ",
                                self.repoShortname(subrepo.split(":")[-1]),
                                subdef.name,
                                " failed",
                            )
                            return False

        print("Building", self.repoShortname(reponame.split(":")[-1]), testDef.name)

        artifactsNeeded = None

        if testDef.matches.Build:
            artifactsDefined = self.artifactsInTestDef(testDef)
            artifactsRequested = artifactSubsetByBuildName[testDef.name]

            # determine if we just want to run a subset of the stages in the build.
            if (
                artifactsDefined
                and set(artifactsRequested) != set(artifactsDefined)
                and artifactsDefined[-1] not in artifactsRequested
            ):
                print(
                    "\tOnly building until we've produced the following: ",
                    artifactSubsetByBuildName[testDef.name],
                )
                artifactsNeeded = artifactSubsetByBuildName[testDef.name]

        worker_state = WorkerStateOverride(
            "test_looper_interactive_", path, self, cores, volumesToExpose
        )

        if nologcapture:
            logfile = sys.stdout
        else:
            logfile_dir = os.path.join(path, "logs")
            worker_state.ensureDirectoryExists(logfile_dir)
            t = time.gmtime()
            log_path = os.path.join(
                logfile_dir,
                "Log-%s-%s-%s-%s-%s-%s.txt"
                % (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec),
            )
            logfile = open(log_path, "w")

            print("\tlogging output to ", log_path)

        if not interactive:

            class Callbacks:
                def __init__(self):
                    self.t0 = time.time()
                    self.total_lines = 0

                def recordArtifactUploaded(self, artifact):
                    pass

                def heartbeat(self, logMessage=None):
                    if logMessage:
                        logfile.write(logMessage)
                        self.total_lines += logMessage.count("\n")
                        if time.time() - self.t0 > 10 and not nologcapture:
                            print(
                                "\t", time.asctime(), " - ", self.total_lines, " logged"
                            )
                            self.t0 = time.time()
                            logfile.flush()

                def terminalOutput(self, output):
                    pass

                def subscribeToTerminalInput(self, callback):
                    pass

                def requestSourceTarballUpload(
                    self, repoName, commitHash, path, platform
                ):
                    pass

            callbacks = Callbacks()
        else:
            callbacks = WorkerState.DummyWorkerCallbacks(localTerminal=True)

        def onStageFinished(artifact):
            print("\tFinished producing artifact", artifact)
            if artifactsNeeded is not None:
                if artifact in artifactsNeeded:
                    artifactsNeeded.remove(artifact)
                if not artifactsNeeded:
                    # condition for early stopping
                    print("Stopping build early after artifact", artifact, "completed.")
                    return True

        callbacks.recordArtifactUploaded = onStageFinished

        if not worker_state.runTest(
            "interactive",
            callbacks,
            testDef,
            interactive,
            command_override=explicit_cmd,
        )[0]:
            print("Build failed. Exiting.")
            return False

        return True

    def info(self, args):
        byName = self.allTestsAndBuildsByName()

        if args.detail:
            for t in sorted(byName):
                if (
                    any([fnmatch.fnmatchcase(t, p) for p in args.testpattern])
                    or not args.testpattern
                ):
                    self.infoForTest(t, byName[t][0], args.all)
        else:
            grid = [["Name", "Type", "Project", "Configuration", "Environment"]]

            for t in sorted(byName):
                if (
                    any([fnmatch.fnmatchcase(t, p) for p in args.testpattern])
                    or not args.testpattern
                ):
                    grid.append(
                        [
                            t,
                            byName[t][0]._which,
                            byName[t][0].project,
                            byName[t][0].configuration,
                            byName[t][0].environment_name,
                        ]
                    )

            printGrid(grid)

    def infoForTest(self, test, testDef, showAll):
        print(test)
        print("  type: ", testDef._which)

        print("  dependencies: ")

        allDeps = dict(testDef.environment.dependencies)
        allDeps.update(testDef.dependencies)

        for depname, dep in sorted(allDeps.items()):
            if dep.matches.InternalBuild:
                print("    " + depname + ":", dep.name)
            elif dep.matches.ExternalBuild:
                if self.repoShouldBeDisplayed(dep.repo) or showAll:
                    print(
                        "    " + depname + ":",
                        self.repoShortname(dep.repo)
                        + ", commit="
                        + dep.commitHash
                        + ", name="
                        + dep.name,
                    )
            elif dep.matches.Source:
                if self.repoShouldBeDisplayed(dep.repo) or showAll:
                    print(
                        "    " + depname + ":",
                        self.repoShortname(dep.repo) + "/" + dep.commitHash,
                    )
            elif dep.matches.Build:
                if self.repoShouldBeDisplayed(dep.repo) or showAll:
                    print(
                        "    " + depname + ":",
                        self.repoShortname(dep.repo) + ", hash=" + dep.buildHash,
                        ", name=",
                        dep.name,
                    )
            else:
                print("    " + depname + ":", "(unknown!!)", repr(dep))

        def kvprint(key, value, indent):
            if isinstance(value, str) and "\n" in value:
                print(indent + key + ": |")
                print("\n".join([indent + "  " + x for x in value.split("\n")]))
            else:
                print(indent + key + ":" + repr(value))

        print("  variables: ")
        for var, varval in sorted(testDef.variables.items()):
            kvprint(var, varval, "    ")

        toPrint = [
            "name",
            "hash",
            "environment_name",
            "configuration",
            "project",
            "timeout",
            "max_cores",
            "min_cores",
            "min_ram_gb",
        ]

        for key in toPrint:
            kvprint(key, getattr(testDef, key), "  ")

        print("  stages:")
        stage_ix = 0
        for stage in testDef.stages:
            print("    stage %s:" % stage_ix)
            for key in ["order", "command", "cleanup"]:
                kvprint(key, getattr(stage, key), "      ")

            if stage.artifacts:
                print("      artifacts:")
            for artifact in stage.artifacts:
                print("        " + artifact.name + ":")

                kvprint("directory", artifact.name, "          ")
                kvprint(
                    "include_patterns", str(artifact.include_patterns), "          "
                )
                kvprint(
                    "exclude_patterns", str(artifact.exclude_patterns), "          "
                )
                kvprint("format", str(artifact.format), "          ")

    def infoForRepo(self, reponame):
        reponame = self.bestRepo(reponame)

        print("repo: ", self.repoShortname(reponame))

        git_repo = self.getGitRepo(reponame)

        for branchname, sha_hash in git_repo.listBranchesForRemote("origin").items():
            print("\t", branchname, " -> ", sha_hash)

        for branchname in self.branchesCheckedOutForRepo(reponame):
            print(reponame, branchname)
            tests, environments, repos = self.resolver.testEnvironmentAndRepoDefinitionsFor(
                reponame, branchname
            )

            print(branchname)

            print("\tbuilds: ")
            for test, testDef in sorted(tests.items()):
                if testDef.matches.Build:
                    print("\t\t", test)

            print("\ttests: ")
            for test, testDef in sorted(tests.items()):
                if testDef.matches.Test:
                    print("\t\t", test)

            print("\trepos: ")
            for repo, repoDef in sorted(repos.items()):
                if repoDef.matches.Pin:
                    print(
                        "\t\t",
                        repo,
                        "->",
                        "/".join(repoDef.reference.split("/")[:-1] + [repoDef.branch]),
                        "=",
                        repoDef.commitHash(),
                    )

            print("\trepo imports: ")
            for repo, repoDef in sorted(repos.items()):
                if repoDef.matches.ImportedReference:
                    print(
                        "\t\t",
                        repo,
                        "from",
                        repoDef.import_source,
                        "=",
                        repoDef.orig_reference,
                        "=",
                        repoDef.commitHash(),
                    )

            print("\tenvironments: ")
            for envName, envDef in sorted(environments.items()):
                print("\t\t", envName)

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

        paths_visited = set()

        results = []

        def visitRepoRef(ref):
            path = self.checkout_root_path(ref.reponame())

            if path in paths_visited:
                return
            else:
                paths_visited.add(path)

            results.append(
                (
                    path,
                    "/".join(ref.reponame().split(":")[:-1]),
                    ref.reponame().split(":")[-1],
                )
            )

        # walk all the repo definitions and make sure everything is up-to-date
        resolver = TestDefinitionResolverOverride(self, visitRepoRef)
        resolver.testEnvironmentAndRepoDefinitionsFor(ROOT_CHECKOUT_NAME, "HEAD")

        results.append(
            (self.checkout_root_path(ROOT_CHECKOUT_NAME), "src", self.checkout_root[0])
        )

        for path, localname, actualrepo in sorted(results, key=lambda vals: vals[1]):
            f(path, localname, actualrepo)

    def status(self, args):
        if self.checkout_root is None:
            print("Nothing checked out...")
            return

        def printer(path, localname, remotename):
            git = Git.Git(path)

            if args.all or self.repoShouldBeDisplayed(remotename):
                hash = git.currentCheckedOutCommit()

                diffstat = git.currentFileNumStat() if git.isInitialized() else None

                print(
                    "%-50s" % localname, "%-50s" % self.repoShortname(remotename), hash
                )  # , git.branchnameForCommitSloppy(hash)

                if git.isInitialized():
                    diffstat = git.currentFileNumStat()
                    for path in diffstat:
                        print(
                            "\t++ %-5d  -- %-5d   %s"
                            % (diffstat[path][0], diffstat[path][1], path)
                        )
                else:
                    print("\tNOT INITIALIZED")

        self.walkCheckedOutRepos(printer)


def main(argv):
    try:
        Git.Git.versionCheck()
    except UserWarning as e:
        print("Error:\n\n%s" % str(e))
        return 1

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
                elif parsedArgs.command == "info":
                    ctl.info(parsedArgs)
                elif parsedArgs.command == "run":
                    ctl.run(parsedArgs)
                elif parsedArgs.command == "fetch":
                    ctl.fetch(parsedArgs)
                elif parsedArgs.command == "status":
                    ctl.status(parsedArgs)
                else:
                    raise UserWarning("Unknown command " + parsedArgs.command)
            finally:
                ctl.writeState()

    except UserWarning as e:
        print("Error:\n\n%s" % str(e))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
