import cPickle as pickle
import logging
import subprocess
import traceback
import os
import sys
import threading
import time
import tempfile
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
                return pickle.dumps(subprocess.check_call(*self.args, **self.kwds))
            except subprocess.CalledProcessError as e:
                return pickle.dumps(e.returncode)

class SubprocessCheckOutput(object):
    def __init__(self, path, args, kwds):
        self.path = path
        self.args = args
        self.kwds = kwds

    def __call__(self):
        with DirectoryScope.DirectoryScope(self.path):
            return pickle.dumps(subprocess.check_output(*self.args, **self.kwds))

sha_pattern = re.compile("^[a-f0-9]{40}$")
def isShaHash(committish):
    return sha_pattern.match(committish)

_outOfProcessDownloaderPoolLock = threading.Lock()
_outOfProcessDownloaderPool = [None]

class Git(object):
    def __init__(self, path_to_repo):
        assert isinstance(path_to_repo, (str, unicode))
        
        self.path_to_repo = str(path_to_repo)
        
        with _outOfProcessDownloaderPoolLock:
            if _outOfProcessDownloaderPool[0] is None:
                _outOfProcessDownloaderPool[0] = \
                    OutOfProcessDownloader.OutOfProcessDownloaderPool(4, actuallyRunOutOfProcess=sys.platform != "win32")
            self.outOfProcessDownloaderPool = _outOfProcessDownloaderPool[0]

        self.git_repo_lock = threading.RLock()

        self.testDefinitionLocationCache_ = {}

    def writeFile(self, name, text):
        with open(os.path.join(self.path_to_repo, name), "w") as f:
            f.write(text)

    def listRemotes(self):
        return [x.strip() for x in self.subprocessCheckOutput(["git","remote"]).split("\n") if x.strip() != '']

    def pullLatest(self):
        remotes = self.listRemotes()
        if "origin" in remotes:
            return self.subprocessCheckCall(['git' ,'fetch', 'origin', '-p']) == 0
        else:
            return True

    def resetToCommit(self, revision):
        logging.info("Resetting to revision %s", revision)

        if not self.pullLatest():
            return False

        return self.subprocessCheckCall(['git', 'reset','--hard', revision]) == 0

    def resetToCommitInDirectory(self, revision, directory):
        with self.git_repo_lock:

            assert isinstance(revision, str), revision

            directory = os.path.abspath(directory)

            self.ensureDirectoryExists(directory)

            logging.info("Resetting to revision %s in %s", revision, directory)

            if not self.pullLatest():
                raise Exception("Couldn't pull latest from origin")

            if self.subprocessCheckCall(
                    ['git', 'worktree', 'add', '--detach', directory, revision]
                    ):
                raise Exception("Failed to create working tree at %s" % directory)

            if self.subprocessCheckCallAltDir(
                    directory,
                    ["git", "reset", "--hard", revision]
                    ):
                raise Exception("Failed to checkout revision %s" % revision)

    def ensureDirectoryExists(self, path):
        if os.path.exists(path):
            return
        try:
            os.makedirs(path)
        except os.error as e:
            if e.errno != errno.EEXIST:
                raise

    def createCommit(self, commitHash, fileContents, commit_message, timestamp_override=None, author="test_looper <test_looper@test_looper.com>"):
        """Create a new commit.

        fileContents - a dictionary from path to string or None. None means delete.
        """
        with self.git_repo_lock:
            tmpdir = tempfile.mkdtemp()
            try:
                self.resetToCommitInDirectory(commitHash, tmpdir)
                for file, contents in fileContents.iteritems():
                    path = os.path.join(tmpdir, file)
                        
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
                return self.commit(commit_message, timestamp_override, author, tmpdir)
            finally:
                shutil.rmtree(tmpdir)

    def pushCommit(self, commitHash, branch, force=False, createBranch=False):
        """push a sha-hash to a branch and return success"""
        assert commitHash and isinstance(commitHash, str)
        assert commitHash.isalnum()
        assert " " not in branch

        if createBranch:
            branch = "refs/heads/" + branch

        return self.subprocessCheckCall(["git", "push", "origin", "%s:%s" % (commitHash, branch)] + (["-f"] if force else [])) == 0

    def commit(self, msg, timestamp_override=None, author="test_looper <test_looper@test_looper.com>", dir_override=None):
        """Commit the current state of the repo and return the commit id"""
        if dir_override is None:
            dir_override = self.path_to_repo

        assert self.subprocessCheckCallAltDir(dir_override, ["git", "add", "."]) == 0

        env = dict(os.environ)

        if timestamp_override:
            timestamp_override_options = ["--date", str(timestamp_override) + " -0500"]
            env["GIT_COMMITTER_DATE"] = str(timestamp_override) + " -0500"
        else:
            timestamp_override_options = []

        cmds = ["git", "commit", "--allow-empty", "-m", msg] + timestamp_override_options + ["--author", author]

        assert self.subprocessCheckCallAltDir(dir_override, cmds, env=env) == 0

        return self.subprocessCheckOutputAltDir(dir_override, ["git", "log", "-n", "1", '--format=format:%H'])

    def isInitialized(self):
        logging.debug('Checking existence of %s', os.path.join(self.path_to_repo, ".git"))
        return os.path.exists(os.path.join(self.path_to_repo, ".git"))

    def ensureDetached(self):
        self.subprocessCheckCall(["git", "checkout", "--detach", "HEAD"])

    def init(self):
        if not os.path.exists(self.path_to_repo):
            os.makedirs(self.path_to_repo)

        with self.git_repo_lock:
            if self.subprocessCheckCall(['git', 'init', '.']) != 0:
                msg = "Failed to init repo at %s" % self.path_to_repo
                logging.error(msg)
                raise Exception(msg)


    def cloneFrom(self, sourceRepo):
        if not os.path.exists(self.path_to_repo):
            os.makedirs(self.path_to_repo)

        with self.git_repo_lock:
            res = self.subprocessCheckCall(['git', 'clone', sourceRepo, '.'])
            if res != 0:
                logging.error("Failed to clone source repo %s: git returned %s", sourceRepo, res)
                return False

        return True

    def listBranches(self):
        with self.git_repo_lock:
            output = self.subprocessCheckOutput(['git','branch','--list']).strip().split('\n')
            
            output = [l.strip() for l in output if l]
            output = [l[1:] if l[0] == '*' else l for l in output if l]
            output = [l.strip() for l in output if l]

            return [l for l in output if l and self.isValidBranchName_(l)]
    
    def branchnameForCommitSloppy(self, hash, remoteName="origin", maxSearchDepth=1000):
        """Try to return a name for the commit relative to a branch.

        Note that this doesn't handle merge commits correctly
        """
        branches = []
        for b in self.listBranchesForRemote(remoteName):
            hashes = [x[0] for x in self.hashParentsAndCommitTitleForMulti("origin/" + b, maxSearchDepth)]

            if hash in hashes:
                ix = hashes.index(hash)
                branches.append((ix,b))

        if branches:
            branches = sorted(branches)
            ix, branch = branches[0]
            if ix == 0:
                return branch
            else:
                return branch + "~%s" % ix

    def listBranchesForRemote(self, remote):
        with self.git_repo_lock:
            lines = self.subprocessCheckOutput(['git', 'branch', '-r']).strip().split('\n')
            lines = [l[:l.find(" -> ")].strip() if ' -> ' in l else l.strip() for l in lines]
            lines = [l[7:].strip() for l in lines if l.startswith("origin/")]

            return [r for r in lines if r != "HEAD"]

    def standardCommitMessageFor(self, commitHash):
        with self.git_repo_lock:
            return self.subprocessCheckOutput(
                    ["git", "log", "-n", "1", commitHash]
                    )

    def hashParentsAndCommitTitleFor(self, commitHash):
        return self.hashParentsAndCommitTitleForMulti(commitHash, depth=1)[0]

    def hashParentsAndCommitTitleForMulti(self, commitHash, depth):
        with self.git_repo_lock:
            data = None
            try:
                uuid = '--e1ecbbfadd6e429892fc34602289e93c'

                result = []

                commandResult = self.subprocessCheckOutput(
                    ["git", "--no-pager", "log", "-n", str(depth), "--topo-order", commitHash, '--format=format:%H %P--%ct--%B' + uuid]
                    )

                for data in commandResult.split(uuid):
                    data = data.strip()
                    if data:
                        commits, timestamp, message = data.split('--', 2)
                        commits = [c.strip() for c in commits.split(" ") if c.strip()]

                        result.append((commits[0], commits[1:], timestamp, message.strip()))

                return result
            except:
                logging.error("Failed to get git info on %s. data=%s", commitHash, data)
                raise

    def getFileContents(self, commit, path):
        with self.git_repo_lock:
            try:
                return self.subprocessCheckOutput(["git", "show", "%s:%s" % (commit,path)])
            except:
                return None

    def commitExists(self, commitHash):
        try:
            return commitHash in self.subprocessCheckOutput(["git", "rev-parse", "--quiet", "--verify", "%s^{commit}" % commitHash])
        except:
            return False

    @staticmethod
    def getTestDefinitionsPathFromDir(checkoutPath):
        all = []
        with DirectoryScope.DirectoryScope(checkoutPath):
            for dirpath, directories, files in os.walk("."):
                if not dirpath.startswith("./.git"):
                    for f in files:
                        if f in ("testDefinitions.json" , "testDefinitions.yml") or f.endswith(".testlooper.yml"):
                            all.append(os.path.join(dirpath, f))

        all = sorted(all)
        if not all:
            return None
        return all[0]

    def getTestDefinitionsPath(self, commit):
        """Breadth-first search through the git repo to find testDefinitions.json"""
        if not self.commitExists(commit):
            logging.info("Commit %s doesn't exist in %s Pulling to see if we can find it.", commit, self.path_to_repo)
            self.pullLatest()

            if not self.commitExists(commit):
                logging.warn("Commit %s doesn't exist in %s even after pulling from origin.", commit, self.path_to_repo)
                raise Exception("Can't find commit %s" % commit)
        
        if commit in self.testDefinitionLocationCache_:
            return self.testDefinitionLocationCache_.get(commit)

        paths = sorted(
            [p for p in
                self.subprocessCheckOutput(["git", "ls-tree", "--name-only", "-r", commit]).split("\n")
                if p.endswith("/testDefinitions.json") or p == "testDefinitions.json" or 
                   p.endswith("/testDefinitions.yml") or p == "testDefinitions.yml" or
                   p.endswith(".testlooper.yml")]
            )

        logging.debug("For commit %s, found testDefinitions at %s", commit, paths)

        if not paths:
            self.testDefinitionLocationCache_[commit] = None
        else:
            self.testDefinitionLocationCache_[commit] = paths[0]

        return self.testDefinitionLocationCache_[commit]

    def fetchOrigin(self):
        with self.git_repo_lock:
            if self.subprocessCheckCall(['git', 'fetch', '-p']) != 0:
                logging.error("Failed to fetch from origin!")


    @staticmethod
    def isValidBranchName_(name):
        return name and '/HEAD' not in name and "(" not in name and "*" not in name


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
            
        with open(path,"r") as f:
            return f.read()


