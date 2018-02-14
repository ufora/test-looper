"""
RemoteRepo

Represents a Git repo hosted by a service such as Github, Bitbucket, etc.
"""

from test_looper.core.tools.Git import Git
import logging
import os

def isValidRepoName(name):
    for c in name:
        if not (c.isalnum() or c in "-_/"):
            return False
    return True


class RemoteRepo(object):
    def __init__(self, name, path_to_local_repo, source_control):
        assert isValidRepoName(name), "Invalid reponame: %s" % name

        self.name = name
        self.source_repo = Git(path_to_local_repo)
        self.source_control = source_control

    def convertRefToHash(self, branchOrHash):
        hashChar = "0123456789abcdefABCDEF"
        if len(branchOrHash) != 40 or [x for x in branchOrHash if x not in hashChar]:
            return "origin/" + branchOrHash
        return branchOrHash

    def commitsLookingBack(self, branchOrHash, depth):
        tuples = []

        branchOrHash = self.convertRefToHash(branchOrHash)

        if not self.source_repo.commitExists(branchOrHash):
            return []

        return self.source_repo.gitCommitDataMulti(branchOrHash, depth=depth)
    
    def listBranches(self):
        return self.source_repo.listBranchesForRemote("origin")

    def branchTopCommit(self, branch):
        return self.source_repo.gitCommitData("origin/" + branch)[0]

    def getTestScriptDefinitionsForCommit(self, commitHash):
        test_definitions_path = self.source_repo.getTestDefinitionsPath(commitHash)

        if test_definitions_path is None:
            return None, None

        return self.source_repo.getFileContents(commitHash, test_definitions_path), os.path.splitext(test_definitions_path)[1]

    def cloneUrl(self,):
        """The clone url for a the repo"""
        return self.source_control.cloneUrl(self.name)

    def ensureInitialized(self):
        if not self.source_repo.isInitialized():
            logging.info("Cloning copy of repo %s", self.cloneUrl())
            self.source_repo.cloneFrom(self.cloneUrl())

    def refresh(self):
        self.source_repo.fetchOrigin()        
