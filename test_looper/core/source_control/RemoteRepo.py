"""
RemoteRepo

Represents a Git repo hosted by a service such as Github, Bitbucket, etc.
"""

def isValidRepoName(name):
    for c in name:
        if not (c.isalnum() or c in "-_"):
            return False
    return True

class RemoteRepo(object):
    def __init__(self, name):
        assert isValidRepoName(name)

        self.name = name

    def listBranches(self):
        """a list of branchnames we export"""
        assert False, "subclasses implement"

    def commitsLookingBack(self, branch, depth):
        """a list of commits looking at first parents going back 'depth'"""
        assert False, "subclasses implement"

    def commitsBetweenBranches(self, branch, baseline):
        """a list of commits in between two branches"""
        assert False, "subclasses implement"

    def getTestScriptDefinitionsForCommit(self, commitId):
        """The test script definition text for a given commit"""
        assert False, "subclasses implement"

    def commit_url(self, commit):
        """The url to show the contents of a given commit. None if not available"""
        assert False, "subclasses implement"

    def cloneUrl(self):
        """The clone url for a the repo"""
        assert False, "subclasses implement"

    def refresh(self):
        """Refresh the local repo view"""