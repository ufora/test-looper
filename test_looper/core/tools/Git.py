import cPickle as pickle
import logging
import subprocess
import traceback
import os
import threading
import time

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
        assert isinstance(path_to_repo, (str, unicode))
        
        self.path_to_repo = str(path_to_repo)
        
        self.outOfProcessDownloaderPool = \
            OutOfProcessDownloader.OutOfProcessDownloaderPool(1, dontImportSetup=True)
        
        self.git_repo_lock = threading.RLock()

        self.testDefinitionLocationCache_ = {}

    def writeFile(self, name, text):
        with open(os.path.join(self.path_to_repo, name), "w") as f:
            f.write(text)

    def listRemotes(self):
        return [x.strip() for x in self.subprocessCheckOutput("git remote",shell=True).split("\n")]

    def pullLatest(self):
        remotes = self.listRemotes()
        if "origin" in remotes:
            return self.subprocessCheckCall(['git fetch origin'], shell=True) == 0
        else:
            return True

    def resetToCommit(self, revision):
        logging.info("Resetting to revision %s", revision)

        if not self.pullLatest():
            return False

        return self.subprocessCheckCall('git reset --hard ' + revision, shell=True) == 0

    def resetToCommitInDirectory(self, revision, directory):
        directory = os.path.abspath(directory)

        logging.info("Resetting to revision %s in %s", revision, directory)

        if not self.pullLatest():
            raise Exception("Couldn't pull latest from origin")

        if self.subprocessCheckCall(
                ['git', 'worktree', 'add', '--detach', directory]
                ):
            raise Exception("Failed to create working tree at %s" % directory)

        if self.subprocessCheckCallAltDir(
                directory,
                "git reset --hard " + revision,
                shell=True
                ):
            raise Exception("Failed to checkout revision %s" % revision)

    def commit(self, msg, timestamp_override=None, author="test_looper <test_looper@test_looper.com>"):
        """Commit the current state of the repo and return the commit id"""
        assert self.subprocessCheckCall(["git", "add", "."]) == 0

        env = dict(os.environ)

        if timestamp_override:
            timestamp_override_options = ["--date", str(timestamp_override) + " -0500"]
            env["GIT_COMMITTER_DATE"] = str(timestamp_override) + " -0500"
        else:
            timestamp_override_options = []

        cmds = ["git", "commit", "-m", msg] + timestamp_override_options + ["--author", author]

        assert self.subprocessCheckCall(cmds, env=env) == 0

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
            output = self.subprocessCheckOutput('git branch --list', shell=True).strip().split('\n')
            
            output = [l.strip() for l in output if l]
            output = [l[1:] if l[0] == '*' else l for l in output if l]
            output = [l.strip() for l in output if l]

            return [l for l in output if l and self.isValidBranchName_(l)]
            
    def listBranchesForRemote(self, remote):
        with self.git_repo_lock:
            lines = self.subprocessCheckOutput('git branch -r', shell=True).strip().split('\n')
            lines = [l[:l.find(" -> ")].strip() if ' -> ' in l else l.strip() for l in lines]
            lines = [l[7:].strip() for l in lines if l.startswith("origin/")]

            return [r for r in lines if r != "HEAD"]

    def hashParentsAndCommitTitleFor(self, commitId):
        with self.git_repo_lock:
            command = 'git --no-pager log -n 1 --topo-order {commitId} --format=format:"%H %P -- %s"'

            data = self.subprocessCheckOutput(
                ["git", "--no-pager", "log", "-n", "1", "--topo-order", commitId, '--format=format:%H %P -- %s']
                ).strip()

            commits, message = data.split(' -- ', 1)
            commits = [c.strip() for c in commits.split(" ") if c.strip()]

            return commits[0], commits[1:], message

    def commitsInRevList(self, commitRange):
        """
        Returns the list of commits in the specified range.

        'commitRange' should be a revlist, e.g.

            origin/master ^origin/master^^^^^^

        Resulting objects are tuples of
            (hash, (parent1_hash, parent2_hash, ...), title)
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

    def commitExists(self, commitHash):
        return commitHash in self.subprocessCheckOutput("git rev-parse --quiet --verify %s^{commit}" % commitHash, shell=True)

    def getTestDefinitionsPath(self, commit):
        """Breadth-first search through the git repo to find testDefinitions.json"""

        if commit in self.testDefinitionLocationCache_:
            return self.testDefinitionLocationCache_.get(commit)
        
        paths = sorted(
            [p for p in (
                self.subprocessCheckOutput(["git", "ls-files", "*/testDefinitions.json"]).split("\n")+
                self.subprocessCheckOutput(["git", "ls-files", "*/testDefinitions.yaml"]).split("\n")+
                self.subprocessCheckOutput(["git", "ls-files", "testDefinitions.json"]).split("\n")+
                self.subprocessCheckOutput(["git", "ls-files", "testDefinitions.yaml"]).split("\n")
                ) if "testDefinitions.json" in p or "testDefinitions.yaml" in p]
            )

        if not paths:
            self.testDefinitionLocationCache_[commit] = None
        else:
            self.testDefinitionLocationCache_[commit] = paths[0]

        return self.testDefinitionLocationCache_[commit]

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

    def pruneRemotes(self):
        raise Exception("Can't modify a Locked git repo")

    def fetchOrigin(self):
        pass

    def pullOrigin(self):
        pass

    def getFileContents(self, commit, path):
        assert commit == "<working_copy>"

        path = os.path.join(self.path_to_repo, path)
        if not os.path.exists(path):
            return None
            
        with open(path,"r") as f:
            return f.read()


