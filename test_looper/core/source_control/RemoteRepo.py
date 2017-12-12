"""
RemoteRepo

Represents a Git repo hosted by a service such as Github, Bitbucket, etc.
"""

def isValidRepoName(name):
    for c in name:
        if not (c.isalnum() or c in "-_/"):
            return False
    return True

class RemoteRepo(object):
    def __init__(self, name, source_repo):
        assert isValidRepoName(name)

        self.name = name
        self.source_repo = source_repo

    def convertRefToHash(self, branchOrHash):
        return branchOrHash

    def commitsLookingBack(self, branchOrHash, depth):
        tuples = []

        tuples.append(self.source_repo.hashParentsAndCommitTitleFor(self.convertRefToHash(branchOrHash)))

        parents = list(tuples[-1][1])

        seen = set()
        seen.add(tuples[0][0])

        while len(tuples) < depth and parents:
            if parents[0] not in seen:
                tuples.append(self.source_repo.hashParentsAndCommitTitleFor(parents[0]))
                seen.add(parents[0])
                parents.extend(tuples[-1][1])
            parents.pop(0)

        return tuples
    
    def listBranches(self):
        """a list of branchnames we export"""
        assert False, "subclasses implement"

    def commitsBetweenBranches(self, branch, baseline):
        """a list of commits in between two branches"""
        assert False, "subclasses implement"

    def branchTopCommit(self, branch):
        commits = self.commitsBetweenBranches(branch, branch + "^")
        if commits:
            assert len(commits) >= 1
            return commits[0][0]


    def getTestScriptDefinitionsForCommit(self, repoName, commitHash):
        """The test script definition text for a given commit"""
        assert False, "subclasses implement"

    def commit_url(self, commitHash):
        """The url to show the contents of a given commit. None if not available"""
        assert False, "subclasses implement"

    def cloneUrl(self):
        """The clone url for a the repo"""
        assert False, "subclasses implement"

    def refresh(self):
        """Refresh the local repo view"""