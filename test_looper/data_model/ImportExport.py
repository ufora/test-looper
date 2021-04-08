import collections
import logging
import random
import time
import traceback
import json
import threading
import textwrap
import re
from test_looper.core.hash import sha_hash
import test_looper.core.Bitstring as Bitstring
import test_looper.core.object_database as object_database
import test_looper.core.algebraic as algebraic
import test_looper.data_model.Types as Types


def makeDict(**args):
    return args


class DictWrapper:
    def __init__(self, jsonDict):
        self._jsonDict = jsonDict

    def __getattr__(self, attr):
        res = self._jsonDict[attr]
        if isinstance(res, dict):
            return DictWrapper(res)
        return res

    def __getitem__(self, x):
        res = self._jsonDict[x]
        if isinstance(res, dict):
            return DictWrapper(res)
        return res

    def __len__(self):
        return len(self._jsonDict)

    def __iter__(self):
        return self._jsonDict.__iter__()

    def items(self):
        for k, v in self._jsonDict.items():
            if isinstance(v, dict):
                yield k, DictWrapper(v)
            else:
                yield k, v


ImportError = algebraic.Alternative("ImportError")
ImportError.UnknownRepo = makeDict(repo=str)
ImportError.UnknownBranch = makeDict(repo=str, name=str)
ImportError.UnknownCommit = makeDict(repo=str, hash=str)
ImportError.UnknownTest = makeDict(repo=str, hash=str, test=str)
ImportError.TestAlreadyExists = makeDict(identity=str)


class ImportExport(object):
    """
    Convert the state of the database to a form that just represents the state but none of the
    computed information.
    """

    def __init__(self, testManager):
        self.testManager = testManager
        self.database = self.testManager.database

    def export(self):
        with self.database.view():
            repos = {}
            testNameSets = {}

            testIsAlreadyDumped = set()

            def walkTest(t, testDict):
                runList = []

                testDict[t.testDefinitionSummary.name] = makeDict(
                    runsDesired=t.runsDesired, runs=runList
                )

                for run in self.database.TestRun.lookupAll(test=t):
                    if run.testNames and run.testNames.shaHash not in testNameSets:
                        testNameSets[run.testNames.shaHash] = run.testNames.test_names

                    if not run.canceled:
                        runList.append(
                            makeDict(
                                identity=run._identity,
                                startedTimestamp=run.startedTimestamp,
                                lastHeartbeat=run.lastHeartbeat,
                                endTimestamp=run.endTimestamp,
                                success=run.success,
                                canceled=run.canceled,
                                testNames=run.testNames.shaHash
                                if run.testNames
                                else "",
                                testStepNameIndex=run.testStepNameIndex,
                                testStepTimeStarted=[
                                    x.val if x.matches.Value else None
                                    for x in run.testStepTimeStarted
                                ],
                                testStepTimeElapsed=[
                                    x.val if x.matches.Value else None
                                    for x in run.testStepTimeElapsed
                                ],
                                testStepSucceeded=run.testStepSucceeded.bits,
                                testStepHasLogs=run.testStepHasLogs.bits,
                                totalTestCount=run.totalTestCount,
                                totalFailedTestCount=run.totalFailedTestCount,
                            )
                        )

            commitsToCheck = set()

            for r in self.database.Repo.lookupAll(isActive=True):
                repos[r.name] = makeDict(branches={}, commits={})

                for branch in self.database.Branch.lookupAll(repo=r):
                    repos[r.name]["branches"][branch.branchname] = {
                        "isUnderTest": branch.isUnderTest
                    }

                    commitsToCheck.add(branch.head)

            totalCommitsChecked = 0

            self.testManager.database.clearCache()

            while commitsToCheck:
                c = commitsToCheck.pop()

                if c and c.hash not in repos[c.repo.name]["commits"] and c.data:
                    totalCommitsChecked += 1
                    if totalCommitsChecked % 1000 == 0:
                        logging.info(
                            "Doing commit #%s: %s",
                            totalCommitsChecked,
                            c.repo.name + "/" + c.hash,
                        )
                        self.testManager.database.clearCache()

                    testDict = {}

                    if self.testManager._commitMightHaveTests(c):
                        for test in self.testManager.allTestsForCommit(c):
                            if test not in testIsAlreadyDumped:
                                walkTest(test, testDict)
                                testIsAlreadyDumped.add(test)

                    for parent in c.data.parents:
                        commitsToCheck.add(parent)

                    repos[c.repo.name]["commits"][c.hash] = {
                        "userEnabledTestSets": c.userEnabledTestSets,
                        "tests": testDict,
                        "hasTestFile": self.testManager._commitMightHaveTests(c)
                        and not c.data.noTestsFound,
                        "parents": [p.hash for p in c.data.parents],
                    }

            return makeDict(repos=repos, testNameSets=testNameSets)

    def importResults(self, results):
        results = DictWrapper(results)
        errors = []

        commitInfoCache = {}

        with self.database.transaction():
            # make sure we have repos and branches
            self.testManager._refreshRepos()

            for reponame, repodef in results.repos.items():
                repo = self.database.Repo.lookupAny(name=reponame)
                if repo:
                    self.testManager._refreshBranches(repo, time.time(), None)
                else:
                    errors.append(ImportError.UnknownRepo(repo=reponame))

        for reponame, repodef in results.repos.items():
            with self.database.transaction():
                logging.info("Starting sync of repo %s", reponame)
                for branchname, branchdef in repodef.branches.items():
                    branch = self.database.Branch.lookupAny(
                        reponame_and_branchname=(reponame, branchname)
                    )
                    if not branch:
                        errors.append(
                            ImportError.UnknownBranch(repo=reponame, name=branchname)
                        )
                    else:
                        branch.isUnderTest = branchdef.isUnderTest

            seen = 0
            try:
                transaction = self.database.transaction()
                transaction.__enter__()

                for hash, commitdef in repodef.commits.items():
                    seen += 1
                    if seen % 100 == 0:
                        transaction.__exit__(None, None, None)
                        logging.info(
                            "Have done %s/%s commits in %s",
                            seen,
                            len(repodef.commits),
                            reponame,
                        )
                        transaction = self.database.transaction()
                        transaction.__enter__()

                    repo = self.database.Repo.lookupAny(name=reponame)
                    commit = self.testManager._lookupCommitByHash(repo, hash)

                    self.testManager._updateSingleCommitData(
                        commit,
                        knownNoTestFile=not commitdef.hasTestFile,
                        commitInfoCache=commitInfoCache,
                    )

                    commit.userEnabledTestSets = commitdef.userEnabledTestSets

                    for testname, testdef in commitdef.tests.items():
                        test = commit.data.tests.get(testname)

                        if not test:
                            errors.append(
                                ImportError.UnknownTest(
                                    repo=reponame, hash=hash, test=testname
                                )
                            )
                        else:
                            for run in testdef.runs:
                                errors.extend(
                                    self._importTestRun(
                                        test, DictWrapper(run), results.testNameSets
                                    )
                                )
            finally:
                transaction.__exit__(None, None, None)

        return errors

    def _importTestRun(self, test, run, testNameSets):
        if self.database.TestRun(run.identity).exists():
            return [ImportError.TestAlreadyExists(identity=run.identity)]

        self.testManager._importTestRun(
            test,
            run.identity,
            run.startedTimestamp,
            run.lastHeartbeat,
            run.endTimestamp,
            run.success,
            run.canceled,
            testNameSets[run.testNames] if run.testNames else [],
            run.testStepNameIndex,
            run.testStepTimeStarted,
            run.testStepTimeElapsed,
            run.testStepSucceeded,
            run.testStepHasLogs,
            run.totalTestCount,
            run.totalFailedTestCount,
        )

        return []
