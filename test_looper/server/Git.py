import cPickle as pickle
import logging
import subprocess
import traceback

import test_looper.core.OutOfProcessDownloader as OutOfProcessDownloader

class SubprocessCheckCall(object):
    def __init__(self, args, kwds):
        self.args = args
        self.kwds = kwds

    def __call__(self):
        return pickle.dumps(subprocess.check_call(*self.args, **self.kwds))

class SubprocessCheckOutput(object):
    def __init__(self, args, kwds):
        self.args = args
        self.kwds = kwds

    def __call__(self):
        return pickle.dumps(subprocess.check_output(*self.args, **self.kwds))


class Git(object):
    def __init__(self):
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
            (hash, parent_hash, title, branchName)
        """
        if not commitRange:
            return []

        command = 'git --no-pager log --topo-order ' + \
                commitRange + ' --format=format:"%H %P -- %s"'
        try:
            lines = self.subprocessCheckOutput(command, shell=True).strip().split('\n')
        except subprocess.CalledProcessError:
            stack = ''.join(traceback.format_stack())
            logging.error("error fetching revlist %s\n%s", commitRange, stack)
            raise ValueError("error fetching '%s'" % commitRange)


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

            parent_commit = hashes[1] if len(hashes) == 2 else hashes[2]

            return (
                hashes[0],       # commit hash
                parent_commit,   # parent commit
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
                SubprocessCheckCall(args, kwds)
                )
            )

    def subprocessCheckOutput(self, *args, **kwds):
        return pickle.loads(
            self.outOfProcessDownloaderPool.executeAndReturnResultAsString(
                SubprocessCheckOutput(args, kwds)
                )
            )
