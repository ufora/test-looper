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
    database.BackgroundTask.UpdateCommitPriority = {'commit': database.Commit}


    database.TestPriority = algebraic.Alternative("TestPriority")
    database.TestPriority.UnresolvedDependencies = {}
    database.TestPriority.DependencyFailed = {}
    database.TestPriority.WaitingOnBuilds = {}
    database.TestPriority.NoMoreTests = {}
    database.TestPriority.FirstBuild = {"priority": int}
    database.TestPriority.FirstTest = {"priority": int}
    database.TestPriority.WantsMoreTests = {"priority": int}


    database.DataTask.define(
        task=database.BackgroundTask,
        status=BackgroundTaskStatus
        )

    database.Commit.define(
        hash=str,
        repo=database.Repo,
        data=database.CommitData,
        priority=int
        )

    database.CommitData.define(
        commit=database.Commit,
        parents=algebraic.List(database.Commit),
        subject=str,
        testDefinitions=algebraic.Dict(str, TestDefinition.TestDefinition),
        environments=algebraic.Dict(str, TestDefinition.TestEnvironment),
        testDefinitionsError=str
        )
    database.CommitRelationship.define(
        child=database.Commit,
        parent=database.Commit
        )

    database.Test.define(
        commitData=database.CommitData,
        fullname=str,
        testDefinition=TestDefinition.TestDefinition,
        successes=int,
        totalRuns=int,
        activeRuns=int,
        priority=database.TestPriority,
        runsDesired=int
        )

    database.UnresolvedTestDependency.define(
        test=database.Test,
        dependsOnName=str
        )

    database.UnresolvedSourceDependency.define(
        test=database.Test,
        repo=database.Repo,
        commitHash=str
        )

    database.UnresolvedRepoDependency.define(
        test=database.Test,
        reponame=str,
        commitHash=str
        )

    database.TestDependency.define(
        test=database.Test,
        dependsOn=database.Test
        )

    database.TestRun.define(
        test=database.Test,
        startedTimestamp=float,
        lastHeartbeat=float,
        endTimestamp=float,
        success=bool,
        machine=database.Machine,
        canceled=bool
        )

    database.Repo.define(
        name=str,
        isActive=bool
        )

    database.Branch.define(
        branchname=str,
        repo=database.Repo,
        head=database.Commit,
        isUnderTest=bool
        )

    database.Machine.define(
        machineId=str,
        firstSeen=float,
        lastHeartbeat=float
        )

    database.addIndex(database.DataTask, 'status')
    database.addIndex(database.Machine, 'machineId')
    database.addIndex(database.UnresolvedTestDependency, 'dependsOnName')
    database.addIndex(database.UnresolvedTestDependency, 'test')
    database.addIndex(database.UnresolvedTestDependency, 'test_and_depends', lambda o:(o.test, o.dependsOnName))

    database.addIndex(database.UnresolvedRepoDependency, 'test')
    database.addIndex(database.UnresolvedRepoDependency, 'reponame')
    database.addIndex(database.UnresolvedRepoDependency, 'test_and_reponame', lambda o:(o.test, o.reponame))
    database.addIndex(database.UnresolvedSourceDependency, 'test')
    database.addIndex(database.UnresolvedSourceDependency, 'repo_and_hash', lambda o:(o.repo, o.commitHash))
    database.addIndex(database.UnresolvedSourceDependency, 'test_and_repo_and_hash', lambda o:(o.test, o.repo, o.commitHash))
    database.addIndex(database.TestDependency, 'test')
    database.addIndex(database.TestDependency, 'dependsOn')
    database.addIndex(database.TestDependency, 'test_and_depends', lambda o:(o.test, o.dependsOn))
    database.addIndex(database.Repo, 'name')
    database.addIndex(database.Repo, 'isActive')
    database.addIndex(database.Branch, 'repo')
    database.addIndex(database.Branch, 'head')
    database.addIndex(database.Branch, 'reponame_and_branchname', lambda o: (o.repo.name, o.branchname))
    database.addIndex(database.Commit, 'repo_and_hash', lambda o: (o.repo, o.hash))
    database.addIndex(database.CommitRelationship, 'parent')
    database.addIndex(database.CommitRelationship, 'child')
    database.addIndex(database.Test, 'fullname')
    database.addIndex(database.Test, 'commitData')

    database.addIndex(database.TestRun, 'test')

    database.addIndex(database.Test, 'priority', 
            lambda o: o.priority if (
                    not o.priority.matches.NoMoreTests 
                and not o.priority.matches.UnresolvedDependencies
                and not o.priority.matches.DependencyFailed
                and not o.priority.matches.WaitingOnBuilds)
                else None
            )
