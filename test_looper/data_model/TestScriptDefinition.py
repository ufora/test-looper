"""
TestScriptDefinition

Models a single unit-test script and the resources required to execute it.
"""
import test_looper.core.algebraic as algebraic
import test_looper.core.algebraic_to_json as algebraic_to_json
import logging

TestEnvironment = algebraic.Alternative("TestEnvironment", Docker = {'dockerfile': str})

TestDefinition = algebraic.Alternative("TestDefinition")

TestDefinition.Build = {
    "buildCommand": str,
    "dependencies": algebraic.List(str)
    }
TestDefinition.Test = {
    "testCommand": str,
    "dependencies": algebraic.List(str)
    }
TestDefinition.Deployment = {
    "setupCommand": str,
    "dependencies": algebraic.List(str),
    'portExpose': algebraic.List((str,int))
    }
TestDefinition.Environment = {
    "environment": TestEnvironment,
    "tests": algebraic.List(str)
    }
TestDefinition.ExternalEnvironment = {
    "commitId": str,
    "environmentName": str,
    "tests": algebraic.List(str)
    }

TestDefinition.ImportBuild = {"commitId": str, 'buildName': str}
TestDefinition.ImportSource = {"sourceCommitId": str}
TestDefinition.ImportData = {"rawDataHash": str}
TestDefinition.DependencyGroup = {"tests": algebraic.List(str)}

TestDefinitions = algebraic.Alternative("TestDefinitions")
TestDefinitions.Definitions = {
    'looper_version': int,
    'definitions': algebraic.List((str,TestDefinition))
    }


