import cPickle as pickle
import logging
import subprocess
import traceback
import os

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
    def __init__(self, path_to_repo=None):
        if path_to_repo is None:
            path_to_repo = os.getcwd()

        self.path_to_repo = path_to_repo
        self.outOfProcessDownloaderPool = \
            OutOfProcessDownloader.OutOfProcessDownloaderPool(1, dontImportSetup=True)


    def listBranches(self, prefix='origin'):
        self.fetchOrigin()
        if self.subprocessCheckCall('git remote prune origin', shell=True) != 0:
            logging.error("Failed to 'git remote prune origin'. " +
                          "Deleted remote branches may continue to be tested.")
        output = self.subprocessCheckOutput('git branch -r', shell=True).strip().split('\n')
        output = [l.strip() for l in output if l]
        return [l for l in output if l.startswith(prefix) and self.isValidBranchName_(l)]


    def commitsInRevList(self, commitRange):
        """
        Returns the list of commits in the specified range.

        'commitRange' should be a revlist, e.g.

            origin/master ^origin/master^^^^^^

        Resulting objects are tuples of
            (hash, (parent1_hash, parent2_hash, ...), title, branchName)
        """
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


    def fetchOrigin(self):
        if self.subprocessCheckCall('git fetch', shell=True) != 0:
            logging.error("Failed to fetch from origin!")


    @staticmethod
    def isValidBranchName_(name):
        return name and '/HEAD' not in name


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
