import cPickle as pickle
import logging
import subprocess
import traceback
import os
import threading

import test_looper.core.OutOfProcessDownloader as OutOfProcessDownloader

class DirectoryScope:
    def __init__(self, path):
        self.old_path = None
        self.path = path

    def __enter__(self, *args):
        self.old_path = os.getcwd()
        os.chdir(self.path)

    def __exit__(self, *args):
        os.chdir(self.old_path)


class SubprocessCheckCall(object):
    def __init__(self, path, args, kwds):
        self.path = path
        self.args = args
        self.kwds = kwds

    def __call__(self):
        with DirectoryScope(self.path):
            return pickle.dumps(subprocess.check_call(*self.args, **self.kwds))

class SubprocessCheckOutput(object):
    def __init__(self, path, args, kwds):
        self.path = path
        self.args = args
        self.kwds = kwds

    def __call__(self):
        with DirectoryScope(self.path):
            return pickle.dumps(subprocess.check_output(*self.args, **self.kwds))


class Git(object):
    def __init__(self, path_to_repo):
        assert isinstance(path_to_repo, str)
        
        self.path_to_repo = path_to_repo
        
        self.outOfProcessDownloaderPool = \
            OutOfProcessDownloader.OutOfProcessDownloaderPool(1, dontImportSetup=True)
        
        self.git_repo_lock = threading.RLock()

    def writeFile(self, name, text):
        with open(os.path.join(self.path_to_repo, name), "w") as f:
            f.write(text)

    def pullLatest(self):
        return self.subprocessCheckCall(['git fetch origin'], shell=True) == 0

    def resetToCommit(self, revision):
        logging.info("Resetting to revision %s", revision)

        if not self.pullLatest():
            return False

        return self.subprocessCheckCall('git reset --hard ' + revision, shell=True) == 0

    def commit(self, msg):
        """Commit the current state of the repo and return the commit id"""
        assert self.subprocessCheckCall(["git", "add", "."]) == 0
        assert self.subprocessCheckCall(["git", "commit", "-m", msg]) == 0
        return self.subprocessCheckOutput(["git", "log", "-n", "1", '--format=format:%H'])

    def isInitialized(self):
        return os.path.exists(os.path.join(self.path_to_repo, ".git"))

    def init(self):
        if not os.path.exists(self.path_to_repo):
            os.makedirs(self.path_to_repo)

        with self.git_repo_lock:
            if self.subprocessCheckCall('git init .', shell=True) != 0:
                msg = "Failed to init repo at %s" % self.path_to_repo
                logging.error(msg)
                raise Exception(msg)


    def cloneFrom(self, sourceRepo):
        if not os.path.exists(self.path_to_repo):
            os.makedirs(self.path_to_repo)

        with self.git_repo_lock:
            if self.subprocessCheckCall('git clone %s .' % sourceRepo, shell=True) != 0:
                logging.error("Failed to clone source repo %s")

    def pruneRemotes(self):
        if self.subprocessCheckCall('git remote prune origin', shell=True) != 0:
            logging.error("Failed to 'git remote prune origin'. " +
                              "Deleted remote branches may continue to be tested.")
            
    def listBranches(self):
        with self.git_repo_lock:
            output = self.subprocessCheckOutput('git branch -rl', shell=True).strip().split('\n')
            
            output = [l.strip() for l in output if l]
            output = [l[1:] if l[0] == '*' else l for l in output if l]
            output = [l.strip() for l in output if l]

            return [l for l in output if l and self.isValidBranchName_(l)]
            
    def listBranchesForRemote(self, remote):
        with self.git_repo_lock:
            res = os.listdir(os.path.join(self.path_to_repo,".git","refs","remotes",remote))
            return [r for r in res if r != "HEAD"]

    def commitsInRevList(self, commitRange):
        """
        Returns the list of commits in the specified range.

        'commitRange' should be a revlist, e.g.

            origin/master ^origin/master^^^^^^

        Resulting objects are tuples of
            (hash, (parent1_hash, parent2_hash, ...), title, branchName)
        """
        logging.info("Checking commit range %s", commitRange)

        with self.git_repo_lock:
            if not commitRange:
                return []

            lines = None
            while lines is None:
                try:
                    command = 'git --no-pager log --topo-order ' + \
                            commitRange + ' --format=format:"%H %P -- %s"'

                    lines = self.subprocessCheckOutput(command, shell=True).strip().split('\n')
                except Exception:
                    if commitRange.endswith("^"):
                        commitRange = commitRange[:-1]
                    else:
                        raise Exception("error fetching '%s'" % commitRange)


            lines = [l.strip() for l in lines if l]

            def parseCommitLine(line):
                splitLine = line.split(' -- ')
                if len(splitLine) < 2:
                    logging.warn("Got a confusing commit line: %s", line)
                    return None
                if len(splitLine) > 2:
                    splitLine = [splitLine[0], ' -- '.join(splitLine[1:])]

                hashes = splitLine[0].split(' ')
                if len(hashes) < 2:
                    logging.warn("Got a confusing commit line: %s", line)
                    return None

                return (
                    hashes[0],       # commit hash
                    tuple(hashes[1:]),   # parent commits
                    splitLine[1]     # commit title
                    )

            commitTuples = [parseCommitLine(l) for l in lines]
            return [c for c in commitTuples if c is not None]

    def getFileContents(self, commit, path):
        with self.git_repo_lock:
            try:
                return self.subprocessCheckOutput("git show '%s:%s'" % (commit,path), shell=True)
            except:
                return None


    def fetchOrigin(self):
        with self.git_repo_lock:
            if self.subprocessCheckCall('git fetch', shell=True) != 0:
                logging.error("Failed to fetch from origin!")


    def pullOrigin(self):
        with self.git_repo_lock:
            if self.subprocessCheckCall('git pull', shell=True) != 0:
                logging.error("Failed to pull from origin!")


    @staticmethod
    def isValidBranchName_(name):
        return name and '/HEAD' not in name and "(" not in name and "*" not in name


    def subprocessCheckCall(self, *args, **kwds):
        return pickle.loads(
            self.outOfProcessDownloaderPool.executeAndReturnResultAsString(
                SubprocessCheckCall(self.path_to_repo, args, kwds)
                )
            )

    def subprocessCheckOutput(self, *args, **kwds):
        return pickle.loads(
            self.outOfProcessDownloaderPool.executeAndReturnResultAsString(
                SubprocessCheckOutput(self.path_to_repo, args, kwds)
                )
            )
