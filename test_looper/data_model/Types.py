import test_looper.core.Bitstring as Bitstring
import test_looper.core.algebraic as algebraic
import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.core.Config as Config
import test_looper.core.machine_management.MachineManagement as MachineManagement



BackgroundTaskStatus = algebraic.Alternative("BackgroundTaskStatus")
BackgroundTaskStatus.PendingVeryHigh = {}
BackgroundTaskStatus.PendingHigh = {}
BackgroundTaskStatus.PendingMedium = {}
BackgroundTaskStatus.PendingLow = {}
BackgroundTaskStatus.PendingVeryLow = {}
BackgroundTaskStatus.Running = {}

def setup_types(database):
    database.BackgroundTask = algebraic.Alternative("BackgroundTask")

    database.BackgroundTask.RefreshRepos = {}
    database.BackgroundTask.BootMachineCheck = {}
    database.BackgroundTask.RefreshBranches = {"repo": database.Repo}
    database.BackgroundTask.UpdateBranchPins = {"branch": database.Branch}
    database.BackgroundTask.UpdateBranchTopCommit = {"branch": database.Branch}
    database.BackgroundTask.UpdateCommitData = {"commit": database.Commit}
    database.BackgroundTask.CommitTestParse = {"commit": database.Commit}
    database.BackgroundTask.UpdateTestPriority = {"test": database.Test}
    database.BackgroundTask.UpdateCommitPriority = {'commit': database.Commit}
    database.BackgroundTask.CheckBranchAutocreate = {"branch": database.Branch}

    database.TestPriority = algebraic.Alternative("TestPriority")
    database.TestPriority.WaitingToRetry = {}
    database.TestPriority.DependencyFailed = {}
    database.TestPriority.WaitingOnBuilds = {}
    database.TestPriority.UnresolvedDependencies = {}
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
        status=BackgroundTaskStatus,
        prior=database.DataTask,
        prior_ct=int,
        isHead=bool
        )

    database.Commit.define(
        hash=str,
        repo=database.Repo,
        data=database.CommitData,
        #which testSets have been enabled by a user
        userEnabledTestSets=algebraic.List(str),
        #the test sets that are enabled, assuming that the commit is still visible from a 
        #branch
        calculatedTestSets=algebraic.List(str),
        isReachable=bool
        )

    database.CommitData.define(
        commit=database.Commit,
        parents=algebraic.List(database.Commit),
        subject=str,
        timestamp=int,
        commitMessage=str,
        author=str,
        authorEmail=str,
        tests=algebraic.Dict(str, database.Test),
        repos=algebraic.Dict(str, TestDefinition.RepoReference),
        #map from test_set name to a list of test/build names. Includes all tests that are internal
        #to this commit that are dependencies. This is mostly for display purposes. Prioritization
        #flows from 'testSetsTopLevel'
        testSets=algebraic.Dict(str, algebraic.List(str)),
        #map from test_set name to a list of test/build names that match at the top level (ie. not dependencies)
        testSetsTopLevel=algebraic.Dict(str, algebraic.List(str)),
        #list of triggered test sets
        triggeredTestSets=algebraic.List(str), 
        triggeredTriggers=algebraic.List(str), 
        testDefinitionsError=str,
        testsParsed=bool,
        noTestsFound=bool
        )

    database.CommitTestDependency.define(
        commit=database.Commit,
        test=database.Test
        )

    database.CommitRelationship.define(
        child=database.Commit,
        parent=database.Commit
        )

    database.TestDefinitionSummary = algebraic.Alternative("TestDefinitionSummary")
    database.TestDefinitionSummary.Summary = {
        "name": str,
        "machineOs": MachineManagement.OsConfig,
        "type": str, #Build, Deployment, or Test
        "configuration": str,
        "artifacts": algebraic.List(str),
        "project": str,
        "timeout": int, #max time, in seconds, for the test
        "min_cores": int, #minimum number of cores we should be run on, or zero if we don't care
        "max_cores": int, #maximum number of cores we can take advantage of, or zero
        "min_ram_gb": int, #minimum GB of ram we need to run, or zero if we don't care
        "min_disk_gb": int, #minimum GB of disk space we need to run, or zero if we don't care
        "max_retries": int, #maximum number of times to retry the build
        "retry_wait_seconds": int, #minimum number of seconds to wait before retrying a build
        }

    database.TestResultSummary.define(
        test=database.Test, #the test we belong to
        testNames=database.IndividualTestNameSet, #unique set of test names across this test
        testTotalRuns=algebraic.List(int), #for each test, by index, the total number of times it ran
        testHasLogs=algebraic.List(int), #for each test, by index, how many runs have logs
        testTotalFailures=algebraic.List(int), #for each test, by index, the total number of failures
        avgFailureRate=float, #sum of failures/runs for all tests with runs>0
        totalTestCount=int, #total number of unique tests we have a run for
        testLooksGood=Bitstring.Bitstring, #for each test, does it look good (good now, good last commit, or new)
        testLooksBad=Bitstring.Bitstring, #for each test, does it look bad (bad now, bad last commit, or new)
        testLooksFlakey=Bitstring.Bitstring, #for each test, does it look flakey (flakey now, flakey last commit)
        testLooksBroken=Bitstring.Bitstring, #for each test, does it look broken (bad now, good last commit)
        testLooksFixed=Bitstring.Bitstring, #for each test, does it look fixed (good now, bad/flakey last commit)
        testLooksNew=Bitstring.Bitstring, #for each test, is it new?
        removedTests=database.IndividualTestNameSet, #names of tests that look like they were removed
        testLooksGoodTotal=int,
        testLooksBadTotal=int,
        testLooksFlakeyTotal=int,
        testLooksBrokenTotal=int,
        testLooksFixedTotal=int,
        testLooksNewTotal=int
        )

    database.Test.define(
        hash=str,
        parent=database.Test, #closest test-suite by the same name/configuration/project in a parent commit
        parentChecked=bool, #have we actually been able to check the parent?
        testDefinitionSummary=database.TestDefinitionSummary,
        machineCategory=database.MachineCategory,
        successes=int,
        totalRuns=int,
        activeRuns=int,
        lastTestEndTimestamp=float,
        testResultSummary=database.TestResultSummary,
        #1 if this test is prioritized
        calculatedPriority=int,
        priority=database.TestPriority,
        targetMachineBoot=int, #the number of machines we want to boot to achieve this
        runsDesired=int, #the number of runs the _user_ indicated they wanted
        )

    database.UnresolvedTestDependency.define(
        test=database.Test,
        dependsOnHash=str,
        artifact=str
        )

    database.UnresolvedCommitSourceDependency.define(
        commit=database.Commit,
        repo=database.Repo,
        commitHash=str
        )

    database.UnresolvedCommitRepoDependency.define(
        commit=database.Commit,
        reponame=str
        )

    database.TestDependency.define(
        test=database.Test,
        dependsOn=database.Test,
        artifact=str
        )

    database.TestDependency.define(
        test=database.Test,
        dependsOn=database.Test,
        artifact=str
        )

    database.TestRun.define(
        test=database.Test,
        startedTimestamp=float,
        lastHeartbeat=float,
        endTimestamp=float,
        success=bool,
        artifactsCompleted=algebraic.List(str),
        machine=database.Machine,
        canceled=bool,
        testNames=database.IndividualTestNameSet, #for this run, a lookup table of test names
        testStepNameIndex=algebraic.List(int), #for each step in the run, which test was it
        testStepTimeStarted=algebraic.List(algebraic.Nullable(float)), #for each step in the run, how long did it take.
        testStepTimeElapsed=algebraic.List(algebraic.Nullable(float)), #for each step in the run, how long did it take.
        testStepSucceeded=Bitstring.Bitstring, #for each step in the run, did it succeed?
        testStepHasLogs=Bitstring.Bitstring, #for each step in the run, did it produce individual logs?
        totalTestCount=int,
        totalFailedTestCount=float
        )

    database.IndividualTestNameSet.define(
        shaHash=str,
        test_names=algebraic.List(str)
        )

    database.Repo.define(
        name=str,
        isActive=bool,
        commits=int,
        commitsWithTests=int,
        branchCreateTemplates=algebraic.List(database.BranchCreateTemplate),
        branchCreateLogs=database.LogMessage
        )

    database.BranchCreateTemplate.define(
        globsToInclude=algebraic.List(str),
        globsToExclude=algebraic.List(str),
        suffix=str,
        branchToCopyFrom=str,
        def_to_replace=str,
        disableOtherAutos=bool,
        autoprioritizeBranch=bool,
        deleteOnUnderlyingRemoval=bool
        )

    database.LogMessage.define(
        msg=str,
        timestamp=float,
        prior = database.LogMessage
        )

    database.Branch.define(
        branchname=str,
        repo=database.Repo,
        head=database.Commit,
        isUnderTest=bool,
        autocreateTrackingBranchName=str
        )

    database.BranchPin.define(
        branch=database.Branch,
        repo_def=str,
        pinned_to_repo=str,
        pinned_to_branch=str,
        auto=bool
        )

    database.MachineCategory.define(
        hardware=Config.HardwareConfig,
        os=MachineManagement.OsConfig,
        booted=int,
        desired=int,
        hardwareComboUnbootable=bool,
        hardwareComboUnbootableReason=str
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

    database.addIndex(database.DataTask, 'status', lambda d: d.status if d.isHead else None)
    database.addIndex(database.DataTask, 'pending_boot_machine_check', lambda d: True if d.status.matches.Pending and d.task.matches.BootMachineCheck else None)
    database.addIndex(database.DataTask, 'update_commit_priority', lambda d: 
        d.task.commit if d.task.matches.UpdateCommitPriority else None
        )
    database.addIndex(database.DataTask, 'update_test_priority', lambda d: 
        d.task.test if d.task.matches.UpdateTestPriority else None
        )

    database.addIndex(database.CommitTestDependency, 'test')
    database.addIndex(database.CommitTestDependency, 'commit')

    database.addIndex(database.Machine, 'machineId')

    #don't index the dead ones
    database.addIndex(database.Machine, 'isAlive', lambda m: True if m.isAlive else None)
    database.addIndex(database.Machine, 'hardware_and_os', lambda m: (m.hardware, m.os) if m.isAlive else None)

    database.addIndex(database.MachineCategory, 'hardware_and_os', lambda m: (m.hardware, m.os))
    database.addIndex(database.MachineCategory, 'want_more', lambda m: True if (m.desired > m.booted) else None)
    database.addIndex(database.MachineCategory, 'want_less', lambda m: True if (m.desired < m.booted) else None)

    database.addIndex(database.UnresolvedTestDependency, 'dependsOnHash')
    database.addIndex(database.UnresolvedTestDependency, 'test')
    database.addIndex(database.UnresolvedTestDependency, 'test_and_depends', lambda o:(o.test, o.dependsOnHash, o.artifact))

    database.addIndex(database.UnresolvedCommitRepoDependency, 'commit')
    database.addIndex(database.UnresolvedCommitRepoDependency, 'reponame')
    database.addIndex(database.UnresolvedCommitRepoDependency, 'commit_and_reponame', lambda o:(o.commit, o.reponame))
    database.addIndex(database.UnresolvedCommitSourceDependency, 'commit')
    database.addIndex(database.UnresolvedCommitSourceDependency, 'repo_and_hash', lambda o:(o.repo, o.commitHash))
    database.addIndex(database.UnresolvedCommitSourceDependency, 'commit_and_repo_and_hash', lambda o:(o.commit, o.repo, o.commitHash))

    database.addIndex(database.TestDependency, 'test')
    database.addIndex(database.TestDependency, 'dependsOn')
    database.addIndex(database.TestDependency, 'test_and_depends', lambda o:(o.test, o.dependsOn,o.artifact))
    database.addIndex(database.Repo, 'name')
    database.addIndex(database.Repo, 'isActive')
    database.addIndex(database.Branch, 'repo')
    database.addIndex(database.Branch, 'head')
    database.addIndex(database.Branch, 'reponame_and_branchname', lambda o: (o.repo.name, o.branchname))
    database.addIndex(database.Branch, 'autocreateTrackingBranchName')
    database.addIndex(database.BranchPin, 'branch')
    database.addIndex(database.BranchPin, 'pinned_to', lambda o: (o.pinned_to_repo, o.pinned_to_branch))
    database.addIndex(database.Commit, 'repo_and_hash', lambda o: (o.repo, o.hash))
    database.addIndex(database.CommitRelationship, 'parent')
    database.addIndex(database.CommitRelationship, 'child')
    database.addIndex(database.Deployment, 'isAlive', lambda d: d.isAlive or None)
    database.addIndex(database.Deployment, 'isAliveAndPending', lambda d: d.isAlive and not d.machine or None)
    database.addIndex(database.Deployment, 'runningOnMachine', lambda d: d.machine if d.isAlive else None)
    database.addIndex(database.Test, 'hash')
    database.addIndex(database.Test, 'machineCategoryAndPrioritized',
            lambda o: o.machineCategory if (
                    not o.priority.matches.NoMoreTests 
                and not o.priority.matches.WaitingToRetry
                and not o.priority.matches.DependencyFailed
                and not o.priority.matches.WaitingOnBuilds
                and not o.priority.matches.UnresolvedDependencies
                and not o.priority.matches.HardwareComboUnbootable
                and o.machineCategory)
                else None
            )
    database.addIndex(database.TestRun, 'test')
    database.addIndex(database.TestRun, 'isRunning', lambda t: True if not t.canceled and t.endTimestamp <= 0.0 else None)
    database.addIndex(database.TestRun, 'runningOnMachine', lambda t: t.machine if not t.canceled and t.endTimestamp <= 0.0 else None)

    database.addIndex(database.Test, 'waiting_to_retry',
            lambda o: True if o.priority.matches.WaitingToRetry else None
            )

    database.addIndex(database.Test, 'priority', 
            lambda o: o.priority if (
                    not o.priority.matches.NoMoreTests 
                and not o.priority.matches.WaitingToRetry
                and not o.priority.matches.DependencyFailed
                and not o.priority.matches.WaitingOnBuilds
                and not o.priority.matches.UnresolvedDependencies
                and not o.priority.matches.HardwareComboUnbootable
                )
                else None
            )
