"""
TestScriptDefinition

Models a single unit-test script and the resources required to execute it.
"""
import test_looper.core.algebraic as algebraic
import test_looper.core.algebraic_to_json as algebraic_to_json
import logging

TestDependency = algebraic.Alternative("TestDependency")
TestDependency.Build = {'commitId': str, 'testName': str}
TestDependency.Source = {'commitId': str}
TestDependency.RawData = {'shaHash': str}

TestDependency.add_common_field('exposedAs', str)


TestEnvironment = algebraic.Alternative("TestEnvironment")
TestEnvironment.Docker = {'dockerfile': str}

TestDefinition = algebraic.Alternative("TestDefinition")
TestDefinition.add_common_fields({
    'name': str,
    'env': str,
    'command': str,
    'dependencies': algebraic.List(TestDependency)
    })
TestDefinition.Build = {}
TestDefinition.Test = {}
TestDefinition.Environment = {
    'portExpose': algebraic.List((str,int))
    }


TestDefinitions = algebraic.Alternative("TestDefinitions")

TestDefinitions.Definitions = {
    'environments': algebraic.List((str, TestEnvironment)),
    'tests': algebraic.List(TestEnvironment),
    }
