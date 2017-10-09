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
        self.path_to_repo = path_to_repo
        
        self.outOfProcessDownloaderPool = \
            OutOfProcessDownloader.OutOfProcessDownloaderPool(1, dontImportSetup=True)
        
        self.git_repo_lock = threading.RLock()

    def isInitialized(self):
        return os.path.exists(os.path.join(self.path_to_repo, ".git"))

    def cloneFrom(self, sourceRepo):
        if not os.path.exists(self.path_to_repo):
            os.makedirs(self.path_to_repo)

        with self.git_repo_lock:
            if self.subprocessCheckCall('git clone %s .' % sourceRepo, shell=True) != 0:
                logging.error("Failed to clone source repo %s")

    def listBranches(self):
        with self.git_repo_lock:
            self.fetchOrigin()
            if self.subprocessCheckCall('git remote prune origin', shell=True) != 0:
                logging.error("Failed to 'git remote prune origin'. " +
                              "Deleted remote branches may continue to be tested.")
            output = self.subprocessCheckOutput('git branch -a', shell=True).strip().split('\n')
            
            output = [l.strip() for l in output if l]
            output = [l[1:] if l[0] == '*' else l for l in output if l]
            output = [l.strip() for l in output if l]

            return [l for l in output if l and self.isValidBranchName_(l)]

    def commitsInRevList(self, commitRange):
        """
        Returns the list of commits in the specified range.

        'commitRange' should be a revlist, e.g.

            origin/master ^origin/master^^^^^^

        Resulting objects are tuples of
            (hash, (parent1_hash, parent2_hash, ...), title, branchName)
        """
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
                if len(splitLine) != 2:
                    logging.warn("Got a confusing commit line: %s", line)
                    return None

                hashes = splitLine[0].split(' ')
                if len(hashes) < 2:
                    logging.warn("Got a confusing commit line: %s", line)
                    return None

                return (
                    hashes[0],       # commit hash
                    tuple(hashes[1:]),   # parent commits
                    splitLine[1]     # commit title
                    )

            commitTuples = [parseCommitLine(l) for l in lines if self.isValidBranchName_(l)]
            return [c for c in commitTuples if c is not None]

    def getFileContents(self, commit, path):
        try:
            return self.subprocessCheckOutput("git show '%s:%s'" % (commit,path))
        except Exception as e:
            if "No such file" in e.message:
                return None
            raise


    def fetchOrigin(self):
        with self.git_repo_lock:
            if self.subprocessCheckCall('git fetch', shell=True) != 0:
                logging.error("Failed to fetch from origin!")


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
