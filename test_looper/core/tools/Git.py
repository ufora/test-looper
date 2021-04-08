import pickle
import logging
import subprocess
import traceback
import uuid
import os
import sys
import threading
import errno
import time
import tempfile
import tarfile
import shutil
import re
import test_looper.core.DirectoryScope as DirectoryScope
import test_looper.core.OutOfProcessDownloader as OutOfProcessDownloader


class SubprocessCheckCall(object):
    def __init__(self, path, args, kwds):
        self.path = path
        self.args = args
        self.kwds = kwds

    def __call__(self):
        with DirectoryScope.DirectoryScope(self.path):
            try:
                result = subprocess.check_output(
                    *self.args, stderr=subprocess.STDOUT, **self.kwds
                )
                return pickle.dumps(0)
            except subprocess.CalledProcessError as e:
                return pickle.dumps(e.returncode)


class SubprocessCheckOutput(object):
    def __init__(self, path, args, kwds):
        self.path = path
        self.args = args
        self.kwds = kwds

    def __call__(self):
        try:
            with DirectoryScope.DirectoryScope(self.path):
                return pickle.dumps(
                    subprocess.check_output(
                        *self.args, stderr=subprocess.STDOUT, **self.kwds
                    ).decode("ASCII")
                )
        except subprocess.CalledProcessError as e:
            raise Exception("Failed calling " + str(self.args) + ":\n" + e.output)


sha_pattern = re.compile("^[a-f0-9]{40}$")


def isShaHash(committish):
    return sha_pattern.match(committish)


_outOfProcessDownloaderPoolLock = threading.Lock()
_outOfProcessDownloaderPool = [None]


class Git(object):
    def __init__(self, path_to_repo):
        assert isinstance(path_to_repo, str)

        self.path_to_repo = str(path_to_repo)

        with _outOfProcessDownloaderPoolLock:
            if _outOfProcessDownloaderPool[0] is None:
                _outOfProcessDownloaderPool[
                    0
                ] = OutOfProcessDownloader.OutOfProcessDownloaderPool(
                    8, actuallyRunOutOfProcess=sys.platform != "win32"
                )
            self.outOfProcessDownloaderPool = _outOfProcessDownloaderPool[0]

        self.git_repo_lock = threading.RLock()

        self.testDefinitionLocationCache_ = {}

    def setCoreAutocrlf(self, shouldBeTrue):
        with self.git_repo_lock:
            self.subprocessCheckCall(
                ["git", "config", "core.autocrlf", "true" if shouldBeTrue else "false"]
            )

    @staticmethod
    def versionCheck():
        version = (
            Git(".")
            .subprocessCheckOutput(["git", "--version"])
            .strip()
            .split(" ")[2]
            .split(".")
        )

        try:
            if sys.platform == "win32":
                if (
                    int(version[0]) < 2
                    or int(version[1]) < 16
                    or int(version[2]) < 2
                    or version[3] != "windows"
                ):
                    raise
            else:
                if int(version[0]) < 2 or int(version[1]) < 5:
                    raise
        except:
            if sys.platform == "win32":
                raise Exception(
                    "testlooper requires git version 2.16.2.windows or higher"
                )
            else:
                raise Exception("testlooper requires git 2.5")

    def writeFile(self, name, text):
        with open(os.path.join(self.path_to_repo, name), "w") as f:
            f.write(text)

    def listRemotes(self):
        outp = self.subprocessCheckOutput(["git", "remote"])
        return [x.strip() for x in outp.split("\n") if x.strip() != ""]

    def getUrlForRemote(self, remote="origin"):
        return self.subprocessCheckOutput(["git", "remote", "get-url", remote]).strip()

    def getSourceRepoName(self, remote="origin"):
        url = self.getUrlForRemote(remote)
        if url.startswith("git@"):
            repo = url.split(":")[1]
            if repo.endswith(".git"):
                return repo[:-4]

        raise UserWarning("Can't detect source repo for " + url)

    def pullLatest(self):
        remotes = self.listRemotes()
        if "origin" in remotes:
            return self.subprocessCheckCall(["git", "fetch", "origin", "-p"]) == 0
        else:
            return True

    def resetToCommit(self, revision):
        logging.info("Resetting to revision %s", revision)
        self.setCoreAutocrlf(False)

        if not self.pullLatest():
            return False

        return self.subprocessCheckCall(["git", "reset", "--hard", revision]) == 0

    def checkoutCommit(self, revision):
        logging.info("Checking out revision %s", revision)

        if not self.commitExists(revision):
            return False

        self.setCoreAutocrlf(False)
        return self.subprocessCheckCall(["git", "checkout", revision]) == 0

    def resetToCommitInDirectory(self, revision, directory, setCoreAutocrlf=False):
        with self.git_repo_lock:

            assert isinstance(revision, str), revision

            directory = os.path.abspath(directory)

            self.ensureDirectoryExists(directory)

            logging.info("Resetting to revision %s in %s", revision, directory)

            if not self.pullLatest():
                raise Exception("Couldn't pull latest from origin")

            if setCoreAutocrlf:
                self.setCoreAutocrlf(True)

            if not self.commitExists(revision):
                raise Exception(
                    "Can't find commit %s at %s" % (revision, self.path_to_repo)
                )

            try:
                if self.subprocessCheckCall(
                    ["git", "worktree", "add", "--detach", directory, revision]
                ):
                    raise Exception("Failed to create working tree at %s" % directory)

                if self.subprocessCheckCallAltDir(
                    directory, ["git", "reset", "--hard", revision]
                ):
                    raise Exception("Failed to checkout revision %s" % revision)
            finally:
                self.setCoreAutocrlf(False)

    def ensureDirectoryExists(self, path):
        if os.path.exists(path):
            return
        try:
            os.makedirs(path)
        except os.error as e:
            if e.errno != errno.EEXIST:
                raise

    def createMerge(
        self, commitHash, otherCommits, commit_message, timestamp_override=None
    ):
        with self.git_repo_lock:
            self.resetToCommit(commitHash)

            env = dict(os.environ)

            if timestamp_override:
                timestamp_override_options = [
                    "--date",
                    str(timestamp_override) + " -0500",
                ]
                env["GIT_COMMITTER_DATE"] = str(timestamp_override) + " -0500"
            else:
                timestamp_override_options = []

            cmds = (
                ["git", "merge"]
                + otherCommits
                + ["-m", commit_message]
                + timestamp_override_options
            )

            assert self.subprocessCheckCall(cmds, env=env) == 0

            return self.subprocessCheckOutput(
                ["git", "log", "-n", "1", "--format=format:%H"]
            ).strip()

    def createCommit(
        self,
        commitHash,
        fileContents,
        commit_message,
        timestamp_override=None,
        author="test_looper <test_looper@test_looper.com>",
    ):
        """Create a new commit.

        fileContents - a dictionary of modifications to make. Keys are paths.
            Values are strings of file contents, or None which means to delete the file.
        """
        with self.git_repo_lock:
            self.resetToCommit(commitHash)
            for file, contents in fileContents.items():
                path = os.path.join(self.path_to_repo, file)

                if contents is None:
                    if os.path.exists(path):
                        if os.path.isdir(path):
                            shutil.rmtree(path)
                        else:
                            os.remove(path)
                else:
                    self.ensureDirectoryExists(os.path.split(path)[0])
                    with open(path, "w") as f:
                        f.write(contents)
            return self.commit(commit_message, timestamp_override, author)

    def deleteRemoteBranch(self, branch, remote="origin"):
        return (
            self.subprocessCheckCall(["git", "push", "origin", ":%s" % (branch)]) == 0
        )

    def pushCommit(self, commitHash, branch, force=False, createBranch=False):
        """push a sha-hash to a branch and return success"""
        assert commitHash and isinstance(commitHash, str)
        assert commitHash.isalnum()
        assert " " not in branch

        if createBranch:
            branch = "refs/heads/" + branch

        return (
            self.subprocessCheckCall(
                ["git", "push", "origin", "%s:%s" % (commitHash, branch)]
                + (["-f"] if force else [])
            )
            == 0
        )

    def currentFileDiff(self):
        return [
            x.strip()
            for x in self.subprocessCheckOutput(["git", "diff", "--name-only"]).split(
                "\n"
            )
            if x.strip()
        ]

    def filesChangedBetweenCommits(self, firstCommit, secondCommit):
        return [
            x.strip()
            for x in self.subprocessCheckOutput(
                ["git", "diff", "--name-only", firstCommit, secondCommit]
            ).split("\n")
            if x.strip()
        ]

    def currentFileNumStat(self):
        """Return a dict from path -> (added,removed) diff"""
        pat = re.compile("\\s*(\\d+)\\s+(\\d+)\\s+(.*)\\s*")

        res = {}
        for line in self.subprocessCheckOutput(["git", "diff", "--numstat"]).split(
            "\n"
        ):
            match = pat.match(line)
            if match:
                res[match.groups()[2]] = (
                    int(match.groups()[0]),
                    int(match.groups()[1]),
                )

        return res

    def createInitialCommit(self, commitMessage="Initial commit"):
        with self.git_repo_lock:
            self.setCoreAutocrlf(False)

            branchname = str(uuid.uuid4())

            curHash = self.subprocessCheckOutput(
                ["git", "log", "-n", "1", "--format=format:%H"]
            ).strip()

            try:
                assert (
                    self.subprocessCheckCall(
                        ["git", "checkout", "--orphan", branchname]
                    )
                    == 0
                )
                assert self.subprocessCheckCall(["git", "reset", "--hard"]) == 0
                assert (
                    self.subprocessCheckCall(
                        ["git", "commit", "--allow-empty", "-m", commitMessage]
                    )
                    == 0
                )

                return self.subprocessCheckOutput(
                    ["git", "log", "-n", "1", "--format=format:%H"]
                ).strip()
            finally:
                self.subprocessCheckCall(["git", "checkout", curHash])
                self.subprocessCheckCall(["git", "branch", "-d", branchname])

    def commit(
        self,
        msg,
        timestamp_override=None,
        author="test_looper <test_looper@test_looper.com>",
        dir_override=None,
    ):
        """Commit the current state of the repo and return the commit id"""
        with self.git_repo_lock:
            self.setCoreAutocrlf(False)

            if dir_override is None:
                dir_override = self.path_to_repo

            assert (
                self.subprocessCheckCallAltDir(dir_override, ["git", "add", "."]) == 0
            )

            env = dict(os.environ)

            if timestamp_override:
                timestamp_override_options = [
                    "--date",
                    str(timestamp_override) + " -0500",
                ]
                env["GIT_COMMITTER_DATE"] = str(timestamp_override) + " -0500"
            else:
                timestamp_override_options = []

            cmds = (
                ["git", "commit", "--allow-empty", "-m", msg]
                + timestamp_override_options
                + ["--author", author]
            )

            if self.subprocessCheckCallAltDir(dir_override, cmds, env=env):
                logging.error(
                    "FAILED: %s",
                    self.subprocessCheckOutputAltDir(dir_override, cmds, env=env),
                )
                raise Exception("failed")

            return self.subprocessCheckOutputAltDir(
                dir_override, ["git", "log", "-n", "1", "--format=format:%H"]
            )

    def isInitialized(self):
        logging.debug(
            "Checking existence of %s", os.path.join(self.path_to_repo, ".git")
        )
        return os.path.exists(os.path.join(self.path_to_repo, ".git"))

    def ensureDetached(self):
        self.setCoreAutocrlf(False)
        self.subprocessCheckCall(["git", "checkout", "--detach", "HEAD"])

    def init(self):
        if not os.path.exists(self.path_to_repo):
            os.makedirs(self.path_to_repo)

        with self.git_repo_lock:
            if self.subprocessCheckCall(["git", "init", "."]) != 0:
                msg = "Failed to init repo at %s" % self.path_to_repo
                logging.error(msg)
                raise Exception(msg)

    def cloneFrom(self, sourceRepo):
        if not os.path.exists(self.path_to_repo):
            os.makedirs(self.path_to_repo)

        with self.git_repo_lock:
            res = self.subprocessCheckCall(["git", "clone", sourceRepo, "."])
            if res != 0:
                logging.error(
                    "Failed to clone source repo %s: git returned %s", sourceRepo, res
                )
                return False

        return True

    def listBranches(self):
        with self.git_repo_lock:
            output = (
                self.subprocessCheckOutput(["git", "branch", "--list"])
                .strip()
                .split("\n")
            )

            output = [l.strip() for l in output if l]
            output = [l[1:] if l[0] == "*" else l for l in output if l]
            output = [l.strip() for l in output if l]

            return [l for l in output if l and self.isValidBranchName_(l)]

    def closestBranchFor(self, hash, remoteName="origin", maxSearchDepth=100):
        """Find the branch closest to a given commit"""
        branches = []

        for b in self.listCurrentlyKnownBranchesForRemote(remoteName):
            hashes = [
                x[0] for x in self.gitCommitDataMulti("origin/" + b, maxSearchDepth)
            ]

            if hash in hashes:
                ix = hashes.index(hash)
                branches.append((ix, b))

        if branches:
            branches = sorted(branches)
            ix, branch = branches[0]

            return branch

    def distanceForCommitInBranch(self, hash, branch):
        hashes = [
            x[0] for x in self.gitCommitDataMulti("origin/" + branch, maxSearchDepth)
        ]
        if hash not in hashes:
            return None
        return hashes.find(hash)

    def branchnameForCommitSloppy(self, hash, remoteName="origin", maxSearchDepth=100):
        """Try to return a name for the commit relative to a branch.

        Note that this doesn't handle merge commits correctly
        """
        branches = []

        for b in self.listCurrentlyKnownBranchesForRemote(remoteName):
            hashes = [
                x[0] for x in self.gitCommitDataMulti("origin/" + b, maxSearchDepth)
            ]

            if hash in hashes:
                ix = hashes.index(hash)
                branches.append((ix, b))

        if branches:
            branches = sorted(branches)
            ix, branch = branches[0]
            if ix == 0:
                return branch
            else:
                return branch + "~%s" % ix

    def listCurrentlyKnownBranchesForRemote(self, remote):
        """List the branches we currently know about"""
        with self.git_repo_lock:
            lines = (
                self.subprocessCheckOutput(["git", "branch", "-r"]).strip().split("\n")
            )
            lines = [
                l[: l.find(" -> ")].strip() if " -> " in l else l.strip() for l in lines
            ]
            lines = [l[7:].strip() for l in lines if l.startswith("origin/")]

            return [r for r in lines if r != "HEAD"]

    def listBranchesForRemote(self, remote):
        """Check the remote and return a map from branchname->hash"""
        with self.git_repo_lock:
            lines = (
                self.subprocessCheckOutput(["git", "ls-remote", remote])
                .strip()
                .split("\n")
            )

        res = {}
        for l in lines:
            if l.strip():
                hashcode, refname = l.split("\t", 1)
                hashcode = hashcode.strip()
                refname = refname.strip()
                if refname.startswith("refs/heads/"):
                    res[refname[len("refs/heads/") :]] = hashcode

        return res

    def standardCommitMessageFor(self, commitHash):
        with self.git_repo_lock:
            return self.subprocessCheckOutput(["git", "log", "-n", "1", commitHash])

    def mostRecentHashForSubpath(self, commitHash, subpath):
        with self.git_repo_lock:
            return (
                self.subprocessCheckOutput(
                    [
                        "git",
                        "log",
                        "--format=format:%H",
                        "-n",
                        "1",
                        commitHash,
                        "--",
                        subpath,
                    ]
                )
                or None
            )

    def gitCommitData(self, commitHash):
        """For a commit or revision, returns a tuple
            (hash, [parent hashes], timestamp, commit_summary, author, authorEmail)
        """
        return self.gitCommitDataMulti(commitHash, depth=1)[0]

    def gitCommitDataMulti(self, commitHash, depth):
        with self.git_repo_lock:
            data = None
            try:
                uuid_line = "--" + str(uuid.uuid4()) + "--"
                uuid_item = "--" + str(uuid.uuid4()) + "--"

                result = []

                commandResult = self.subprocessCheckOutput(
                    [
                        "git",
                        "--no-pager",
                        "log",
                        "-n",
                        str(depth),
                        "--topo-order",
                        commitHash,
                        "--format=format:"
                        + uuid_item.join(["%H %P", "%ct", "%B", "%an", "%ae"])
                        + uuid_line,
                    ]
                )

                for data in commandResult.split(uuid_line):
                    if data.strip():
                        commits, timestamp, message, author, authorEmail = data.split(
                            uuid_item
                        )
                        commits = [c.strip() for c in commits.split(" ") if c.strip()]

                        result.append(
                            (
                                commits[0],
                                commits[1:],
                                timestamp,
                                message.strip(),
                                author,
                                authorEmail,
                            )
                        )

                return result
            except:
                logging.error("Failed to get git info on %s. data=%s", commitHash, data)
                raise

    def resetHard(self):
        with self.git_repo_lock:
            self.setCoreAutocrlf(False)
            self.subprocessCheckOutput(["git", "reset", "--hard"])

    def currentCheckedOutCommit(self):
        with self.git_repo_lock:
            try:
                commandResult = self.subprocessCheckOutput(
                    ["git", "--no-pager", "log", "-n", "1", "--format=format:%H"]
                )
                return commandResult.strip()
            except:
                return None

    def getFileContents(self, commit, path):
        with self.git_repo_lock:
            self.setCoreAutocrlf(False)
            try:
                return self.subprocessCheckOutput(
                    ["git", "show", "%s:%s" % (commit, path)]
                )
            except:
                return None

    def commitExists(self, commitHash):
        try:
            return commitHash in self.subprocessCheckOutput(
                ["git", "rev-parse", "--quiet", "--verify", "%s^{commit}" % commitHash]
            )
        except:
            return False

    @staticmethod
    def getTestDefinitionsPathFromDir(checkoutPath):
        all = []
        with DirectoryScope.DirectoryScope(checkoutPath):
            for dirpath, directories, files in os.walk("."):
                if not dirpath.startswith("./.git"):
                    for f in files:
                        if f in (
                            "testDefinitions.json",
                            "testDefinitions.yml",
                            "testlooper.yml",
                        ) or f.endswith(".testlooper.yml"):
                            all.append(os.path.join(dirpath, f))

        all = sorted(all)
        if not all:
            return None
        return all[0]

    def getTestDefinitionsPath(self, commit):
        """Breadth-first search through the git repo to find testDefinitions.json"""
        if not self.commitExists(commit):
            logging.info(
                "Commit %s doesn't exist in %s Pulling to see if we can find it.",
                commit,
                self.path_to_repo,
            )
            self.pullLatest()

            if not self.commitExists(commit):
                logging.warn(
                    "Commit %s doesn't exist in %s even after pulling from origin.",
                    commit,
                    self.path_to_repo,
                )
                raise Exception("Can't find commit %s" % commit)

        if commit in self.testDefinitionLocationCache_:
            return self.testDefinitionLocationCache_.get(commit)

        paths = sorted(
            [
                p
                for p in self.subprocessCheckOutput(
                    ["git", "ls-tree", "--name-only", "-r", commit]
                ).split("\n")
                if p.endswith("/testDefinitions.json")
                or p == "testDefinitions.json"
                or p.endswith("/testDefinitions.yml")
                or p == "testDefinitions.yml"
                or p.endswith(".testlooper.yml")
                or p == "testlooper.yml"
            ]
        )

        logging.debug("For commit %s, found testDefinitions at %s", commit, paths)

        if not paths:
            self.testDefinitionLocationCache_[commit] = None
        else:
            self.testDefinitionLocationCache_[commit] = paths[0]

        return self.testDefinitionLocationCache_[commit]

    def fetchOrigin(self):
        with self.git_repo_lock:
            if self.subprocessCheckCall(["git", "fetch", "-p"]) != 0:
                logging.error("Failed to fetch from origin: %s" % self.path_to_repo)

    @staticmethod
    def isValidBranchName_(name):
        return name and "/HEAD" not in name and "(" not in name and "*" not in name

    def subprocessCheckCallAltDir(self, altDir, *args, **kwds):
        return pickle.loads(
            self.outOfProcessDownloaderPool.executeAndReturnResultAsString(
                SubprocessCheckCall(altDir, args, kwds)
            )
        )

    def subprocessCheckCall(self, *args, **kwds):
        return self.subprocessCheckCallAltDir(self.path_to_repo, *args, **kwds)

    def subprocessCheckOutput(self, *args, **kwds):
        return pickle.loads(
            self.outOfProcessDownloaderPool.executeAndReturnResultAsString(
                SubprocessCheckOutput(self.path_to_repo, args, kwds)
            )
        )

    def subprocessCheckOutputAltDir(self, dir, *args, **kwds):
        return pickle.loads(
            self.outOfProcessDownloaderPool.executeAndReturnResultAsString(
                SubprocessCheckOutput(dir, args, kwds)
            )
        )

    def createRepoTarball(
        self, commitHash, pathWithinRepo, targetTarball, setCoreAutocrlf
    ):
        """Checkout and tarball the subpath 'pathWithinRepo' from commit commitHash and place at targetTarball."""
        workingDir = targetTarball + ".tmpdir"

        try:
            self.resetToCommitInDirectory(
                commitHash, workingDir, setCoreAutocrlf=setCoreAutocrlf
            )
            os.unlink(os.path.join(workingDir, ".git"))

            if pathWithinRepo:
                # we want to checkout path A/B/C (or something like that), which we assume
                # is a directory. If not, the checkout will be empty except for the commit
                subdir = os.path.join(workingDir, pathWithinRepo)

                # if it exists, move the subdirectory to a safe location
                if os.path.isdir(subdir):
                    guid = str(uuid.uuid4()).replace("-", "")
                    movedPath = os.path.join(os.path.dirname(targetTarball), guid)

                    shutil.move(subdir, movedPath)
                else:
                    movedPath = None

                shutil.rmtree(workingDir)

                if movedPath:
                    shutil.move(movedPath, workingDir)
                else:
                    os.makedirs(workingDir)

            with open(os.path.join(workingDir, ".git_commit"), "w") as f:
                f.write(self.standardCommitMessageFor(commitHash))

            with tarfile.open(targetTarball, "w:gz", compresslevel=1) as tf:
                tf.add(workingDir, ".")
        finally:
            try:
                if os.path.exists(workingDir):
                    shutil.rmtree(workingDir)
            except:
                logging.error(
                    "Failed to remove working tree at %s:\n%s",
                    workingDir,
                    traceback.format_exc(),
                )


class LockedGit(Git):
    def __init__(self, path):
        Git.__init__(self, path)

    def writeFile(self, name, text):
        raise Exception("Can't commit in a Locked git repo")

    def pullLatest(self):
        pass

    def resetToCommit(self, revision):
        assert revision == "<working_copy>"
        return True

    def commit(self, msg):
        raise Exception("Can't commit in a Locked git repo")

    def isInitialized(self):
        return True

    def init(self):
        raise Exception("Can't modify a Locked git repo")

    def cloneFrom(self, sourceRepo):
        raise Exception("Can't modify a Locked git repo")

    def fetchOrigin(self):
        pass

    def getFileContents(self, commit, path):
        assert commit == "<working_copy>"

        path = os.path.join(self.path_to_repo, path)
        if not os.path.exists(path):
            return None

        with open(path, "r") as f:
            return f.read()
