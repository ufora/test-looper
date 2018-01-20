import test_looper.core.Bitstring as Bitstring
import test_looper.core.algebraic as algebraic
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.core.Config as Config
import test_looper.core.machine_management.MachineManagement as MachineManagement

BackgroundTaskStatus = algebraic.Alternative("BackgroundTaskStatus")
BackgroundTaskStatus.PendingHigh = {}
BackgroundTaskStatus.PendingLow = {}
BackgroundTaskStatus.Running = {}

def setup_types(database):
    database.BackgroundTask = algebraic.Alternative("BackgroundTask")

    database.BackgroundTask.RefreshRepos = {}
    database.BackgroundTask.BootMachineCheck = {}
    database.BackgroundTask.RefreshBranches = {"repo": database.Repo}
    database.BackgroundTask.UpdateBranchPins = {"branch": database.Branch}
    database.BackgroundTask.UpdateBranchTopCommit = {"branch": database.Branch}
    database.BackgroundTask.UpdateCommitData = {"commit": database.Commit}
    database.BackgroundTask.UpdateTestPriority = {"test": database.Test}
    database.BackgroundTask.UpdateCommitPriority = {'commit': database.Commit}


    database.TestPriority = algebraic.Alternative("TestPriority")
    database.TestPriority.UnresolvedDependencies = {}
    database.TestPriority.DependencyFailed = {}
    database.TestPriority.WaitingOnBuilds = {}
    database.TestPriority.InvalidTestDefinition = {}
    database.TestPriority.HardwareComboUnbootable = {}
    database.TestPriority.NoMoreTests = {}
    database.TestPriority.FirstBuild = {"priority": int}
    database.TestPriority.FirstTest = {"priority": int}
    database.TestPriority.WantsMoreTests = {"priority": int}

    database.FullyResolvedTestEnvironment = algebraic.Alternative("FullyResolvedTestEnvironment")
    database.FullyResolvedTestEnvironment.Unresolved = {}
    database.FullyResolvedTestEnvironment.Error = {"Error": str}
    database.FullyResolvedTestEnvironment.Resolved = {"Environment": TestDefinition.TestEnvironment}

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
        timestamp=int,
        commitMessage=str,
        testDefinitions=algebraic.Dict(str, TestDefinition.TestDefinition),
        environments=algebraic.Dict(str, TestDefinition.TestEnvironment),
        repos=algebraic.Dict(str, TestDefinition.RepoReference),
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
        fullyResolvedEnvironment=database.FullyResolvedTestEnvironment,
        machineCategory=database.MachineCategory,
        successes=int,
        totalRuns=int,
        activeRuns=int,
        totalTestCount=float,
        totalFailedTestCount=float,
        priority=database.TestPriority,
        targetMachineBoot=int, #the number of machines we want to boot to achieve this
        runsDesired=int, #the number of runs the _user_ indicated they wanted
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
        canceled=bool,
        testNames=database.IndividualTestNameSet,
        testFailures=Bitstring.Bitstring, #encoded as an 8-bit bitstring
        totalTestCount=int,
        totalFailedTestCount=int
        )

    database.IndividualTestNameSet.define(
        shaHash=str,
        test_names=algebraic.List(str)
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

    database.BranchPin.define(
        branch=database.Branch,
        repo_def=str,
        pinned_to_repo=str,
        pinned_to_branch=str
        )

    database.MachineCategory.define(
        hardware=Config.HardwareConfig,
        os=MachineManagement.OsConfig,
        booted=int,
        desired=int,
        hardwareComboUnbootable=bool
        )

    database.Machine.define(
        machineId=str,
        hardware=Config.HardwareConfig,
        os=MachineManagement.OsConfig,
        bootTime=float,
        firstHeartbeat=float,
        lastHeartbeat=float,
        lastTestCompleted=float,
        isAlive=bool,
        lastHeartbeatMsg=str
        )

    database.Deployment.define(
        deploymentId=str,
        createdTimestamp=float,
        machine=database.Machine,
        test=database.Test,
        isAlive=bool
        )

    database.addIndex(database.IndividualTestNameSet, 'shaHash')

    database.addIndex(database.DataTask, 'status')
    database.addIndex(database.DataTask, 'pending_boot_machine_check', lambda d: True if d.status.matches.Pending and d.task.matches.BootMachineCheck else None)
    database.addIndex(database.Machine, 'machineId')

    #don't index the dead ones
    database.addIndex(database.Machine, 'isAlive', lambda m: True if m.isAlive else None)
    database.addIndex(database.Machine, 'hardware_and_os', lambda m: (m.hardware, m.os) if m.isAlive else None)

    database.addIndex(database.MachineCategory, 'hardware_and_os', lambda m: (m.hardware, m.os))
    database.addIndex(database.MachineCategory, 'want_more', lambda m: True if (m.desired > m.booted) else None)
    database.addIndex(database.MachineCategory, 'want_less', lambda m: True if (m.desired < m.booted) else None)

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
    database.addIndex(database.BranchPin, 'branch')
    database.addIndex(database.BranchPin, 'pinned_to', lambda o: (o.pinned_to_repo, o.pinned_to_branch))
    database.addIndex(database.Commit, 'repo_and_hash', lambda o: (o.repo, o.hash))
    database.addIndex(database.CommitRelationship, 'parent')
    database.addIndex(database.CommitRelationship, 'child')
    database.addIndex(database.Deployment, 'isAlive', lambda d: d.isAlive or None)
    database.addIndex(database.Deployment, 'isAliveAndPending', lambda d: d.isAlive and not d.machine or None)
    database.addIndex(database.Deployment, 'runningOnMachine', lambda d: d.machine if d.isAlive else None)
    database.addIndex(database.Test, 'fullname')
    database.addIndex(database.Test, 'commitData')
    database.addIndex(database.Test, 'machineCategoryAndPrioritized',
            lambda o: o.machineCategory if (
                    not o.priority.matches.NoMoreTests 
                and not o.priority.matches.UnresolvedDependencies
                and not o.priority.matches.DependencyFailed
                and not o.priority.matches.WaitingOnBuilds
                and not o.priority.matches.HardwareComboUnbootable
                and not o.priority.matches.InvalidTestDefinition
                and o.machineCategory)
                else None
            )
    database.addIndex(database.TestRun, 'test')
    database.addIndex(database.TestRun, 'isRunning', lambda t: True if not t.canceled and t.endTimestamp <= 0.0 else None)
    database.addIndex(database.TestRun, 'runningOnMachine', lambda t: t.machine if not t.canceled and t.endTimestamp <= 0.0 else None)

    database.addIndex(database.Test, 'priority', 
            lambda o: o.priority if (
                    not o.priority.matches.NoMoreTests 
                and not o.priority.matches.UnresolvedDependencies
                and not o.priority.matches.DependencyFailed
                and not o.priority.matches.WaitingOnBuilds
                and not o.priority.matches.HardwareComboUnbootable
                and not o.priority.matches.InvalidTestDefinition)
                else None
            )
