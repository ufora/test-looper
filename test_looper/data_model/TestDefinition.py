"""
TestDefinition

Objects modeling our tests.
"""
import test_looper.core.algebraic as algebraic
import test_looper.core.algebraic_to_json as algebraic_to_json
import logging

Platform = algebraic.Alternative("Platform")
Platform.windows = {}
Platform.linux = {}

Image = algebraic.Alternative("Image")
Image.Dockerfile = {"dockerfile": str}
Image.AMI = {"base_ami": str, "ami_script": str}

TestEnvironment = algebraic.Alternative("TestEnvironment")
TestEnvironment.Environment = {
    "platform": Platform,
    "image": Image,
    "variables": algebraic.Dict(str, str)
    }
TestEnvironment.Import = {
    "repo": str,
    "commitHash": str,
    "name": str
    }

TestDependency = algebraic.Alternative("TestDependency")
TestDependency.InternalBuild = {"name": str, "environment": str}
TestDependency.ExternalBuild = {"repo": str, "commitHash": str, "name": str, "environment": str}
TestDependency.Source = {"repo": str, "commitHash": str}
TestDependency.Data = {"shaHash": str}

TestDefinition = algebraic.Alternative("TestDefinition")
TestDefinition.Build = {
    "buildCommand": str,
    "name": str,
    "environment": TestEnvironment,
    "dependencies": algebraic.Dict(str, TestDependency),
    "variables": algebraic.Dict(str,str)
    }
TestDefinition.Test = {
    "testCommand": str,
    "name": str,
    "environment": TestEnvironment,
    "dependencies": algebraic.Dict(str, TestDependency),
    "variables": algebraic.Dict(str,str)
    }
TestDefinition.Deployment = {
    "deployCommand": str,
    "name": str,
    "environment": TestEnvironment,
    "dependencies": algebraic.Dict(str, TestDependency),
    "variables": algebraic.Dict(str,str),
    'portExpose': algebraic.Dict(str,int)
    }

