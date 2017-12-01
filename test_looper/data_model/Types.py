import test_looper.core.algebraic as algebraic
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.data_model.TestDefinition as TestDefinition

TestDefinitionsOrError = algebraic.Alternative("TestDefinitionsOrError")
TestDefinitionsOrError.Tests = {"tests": TestDefinitionScript.TestDefinitionScript}
TestDefinitionsOrError.Error = {"message": str}



def setup_types(database):
    database.Commit.define(
        hash=str,
        repo=database.Repo,
        data=database.CommitData
        )

    database.CommitData.define(
        parents=algebraic.List(database.Commit),
        testDefinitions=TestDefinitionsOrError,
        testsByType=algebraic.Dict(str, database.TestData)
        )

    database.TestData.define(
        commit=database.Commit,
        testDefinition=TestDefinition.TestDefinition,
        successes=int,
        totalRuns=int,
        priority=int,
        activeRuns=algebraic.List(database.RunningTest),
        completedRuns=algebraic.List(database.CompletedTest)
        )

    database.RunningTest.define(
        testData=database.TestData,
        testId=str,
        startedTimestamp=float,
        lastHeartbeat=float,
        machine=database.Machine
        )

    database.CompletedTest.define(
        testData=databse.TestData,
        testId=str,
        startedTimestamp=float,
        endTimestamp=float,
        machine=database.Machine,
        success=bool
        )

    database.Repo.define(
        name=str,
        branches=algebraic.List(database.Branch)
        )

    database.Branch.define(
        name=str,
        repo=database.Repo,
        head=database.Commit
        )
