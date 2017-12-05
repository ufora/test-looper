import test_looper.core.algebraic as algebraic
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.data_model.TestDefinition as TestDefinition

BackgroundTaskStatus = algebraic.Alternative("BackgroundTaskStatus")
BackgroundTaskStatus.Pending = {}
BackgroundTaskStatus.Running = {}

def setup_types(database):
    database.BackgroundTask = algebraic.Alternative("BackgroundTask")

    database.BackgroundTask.RefreshRepos = {}
    database.BackgroundTask.RefreshBranches = {"repo": database.Repo}
    database.BackgroundTask.UpdateBranchTopCommit = {"branch": database.Branch}
    database.BackgroundTask.UpdateCommitData = {"commit": database.Commit}
    database.BackgroundTask.UpdateTestPriority = {"test": database.Test}

    database.DataTask.define(
        task=database.BackgroundTask,
        status=BackgroundTaskStatus
        )

    database.Commit.define(
        hash=str,
        repo=database.Repo,
        data=database.CommitData
        )

    database.CommitData.define(
        commit=database.Commit,
        parents=algebraic.List(database.Commit),
        subject=str,
        testDefinitionsError=str
        )

    database.Test.define(
        commitData=database.CommitData,
        fullname=str,
        testDefinition=TestDefinition.TestDefinition,
        successes=int,
        totalRuns=int,
        priority=database.TestPriority
        )

    database.UnresolvedTestDependency.define(
        test=database.Test,
        dependsOn=database.Test
        )

    database.ResolvedTestDependency.define(
        test=database.Test,
        dependsOn=database.Test
        )

    database.TestPriority.define(
        testData=database.TestData,
        priorityLevel=int
        )

    database.RunningTest.define(
        testData=database.TestData,
        testId=str,
        startedTimestamp=float,
        lastHeartbeat=float,
        machine=database.Machine
        )

    database.CompletedTest.define(
        testData=database.TestData,
        testId=str,
        startedTimestamp=float,
        endTimestamp=float,
        machine=database.Machine,
        success=bool
        )

    database.Repo.define(
        name=str,
        isActive=bool
        )

    database.Branch.define(
        branchname=str,
        repo=database.Repo,
        head=database.Commit
        )

    database.Machine.define(
        machineId=str,
        firstSeen=float,
        lastHearbeat=float
        )

    database.addIndex(database.DataTask, 'status')
    database.addIndex(database.Machine, 'machineId')
    database.addIndex(database.Repo, 'name')
    database.addIndex(database.Repo, 'isActive')
    database.addIndex(database.Branch, 'repo')
    database.addIndex(database.Commit, 'hash')
    database.addIndex(database.Test, 'fullname')
