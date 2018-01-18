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
Image.DockerfileInline = {"dockerfile_contents": str}
Image.Dockerfile = {"repo": str, "commitHash": str, "dockerfile": str}
Image.AMI = {"base_ami": str, "setup_script_contents": str}

TestDependency = algebraic.Alternative("TestDependency")
TestDependency.InternalBuild = {"name": str, "environment": str}
TestDependency.ExternalBuild = {"repo": str, "commitHash": str, "name": str, "environment": str}
TestDependency.Source = {"repo": str, "commitHash": str}

EnvironmentReference = algebraic.Alternative("EnvironmentReference")
EnvironmentReference.Reference = {"repo": str, "commitHash": str, "name": str}

TestEnvironment = algebraic.Alternative("TestEnvironment")
TestEnvironment.Environment = {
    "environment_name": str,
    "inheritance": algebraic.List(str),
    "platform": Platform,
    "image": Image,
    "variables": algebraic.Dict(str, str),
    "dependencies": algebraic.Dict(str, TestDependency)
    }

TestEnvironment.Import = {
    "environment_name": str,
    "inheritance": algebraic.List(str),
    "imports": algebraic.List(EnvironmentReference),
    "setup_script_contents": str,
    "variables": algebraic.Dict(str, str),
    "dependencies": algebraic.Dict(str, TestDependency)
    }

TestDefinition = algebraic.Alternative("TestDefinition")
TestDefinition.Build = {
    "buildCommand": str,
    "name": str,
    "environment": TestEnvironment,
    "dependencies": algebraic.Dict(str, TestDependency),
    "variables": algebraic.Dict(str,str),
    "disabled": bool, #disabled by default?
    "timeout": int, #max time, in seconds, for the test
    "min_cores": int, #minimum number of cores we should be run on, or zero if we don't care
    "max_cores": int, #maximum number of cores we can take advantage of, or zero
    "min_ram_gb": int, #minimum GB of ram we need to run, or zero if we don't care
    }
TestDefinition.Test = {
    "testCommand": str,
    "cleanupCommand": str,
    "name": str,
    "environment": TestEnvironment,
    "dependencies": algebraic.Dict(str, TestDependency),
    "variables": algebraic.Dict(str,str),
    "disabled": bool, #disabled by default?
    "timeout": int, #max time, in seconds, for the test
    "min_cores": int, #minimum number of cores we should be run on, or zero if we don't care
    "max_cores": int, #maximum number of cores we can take advantage of, or zero
    "min_ram_gb": int, #minimum GB of ram we need to run, or zero if we don't care
    }
TestDefinition.Deployment = {
    "deployCommand": str,
    "name": str,
    "environment": TestEnvironment,
    "dependencies": algebraic.Dict(str, TestDependency),
    "variables": algebraic.Dict(str,str),
    'portExpose': algebraic.Dict(str,int),
    "timeout": int, #max time, in seconds, for the test
    "min_cores": int, #minimum number of cores we should be run on, or zero if we don't care
    "max_cores": int, #maximum number of cores we can take advantage of, or zero
    "min_ram_gb": int, #minimum GB of ram we need to run, or zero if we don't care
    }


def merge_dicts(d1, d2):
    "Return the union of d1 and d2, with keys in d2 taking priority over d1 in case of conflict."
    res = dict(d1)
    for k,v in d2.iteritems():
        res[k] = v
    return res

def add_setup_contents_to_image(image, extra_setup):
    if not extra_setup:
        return image

    assert image.matches.AMI, "Can only add setup-script contents to an AMI image, not %s, and we were given %s" % (image, repr(extra_setup))

    return Image.AMI(base_ami=image.base_ami, setup_script_contents=image.setup_script_contents + "\n" + extra_setup)

def merge_environments(import_environment, underlying_environments):
    """Given an 'Import' environment and its underlying environment, apply the state of the import to the underlying.
    
    This operation is associative: given a chain of imports terminating in a base environment,
    we should be able to apply these changes in any order.
    """
    assert import_environment.matches.Import

    assert len(import_environment.imports) == len(underlying_environments)

    for ix, dep in reversed(list(enumerate(import_environment.imports))):
        underlying_environment = underlying_environments[ix]
        underlying_full_name = dep.repo + "/" + dep.commitHash + "/" + dep.name

        if underlying_environment.matches.Environment:
            import_environment = TestEnvironment.Environment(
                    environment_name=import_environment.environment_name,
                    inheritance=import_environment.inheritance + (underlying_full_name,) + tuple(underlying_environment.inheritance),
                    platform=underlying_environment.platform,
                    image=add_setup_contents_to_image(underlying_environment.image, import_environment.setup_script_contents),
                    variables=merge_dicts(underlying_environment.variables, import_environment.variables),
                    dependencies=merge_dicts(underlying_environment.dependencies, import_environment.dependencies)
                    )
        else:
            import_environment = TestEnvironment.Import(
                environment_name=import_environment.environment_name,
                inheritance=import_environment.inheritance + (underlying_full_name,) + tuple(underlying_environment.inheritance),
                imports=import_environment.imports[:-1] + underlying_environment.imports,
                setup_script_contents=
                    underlying_environment.setup_script_contents + "\n" + import_environment.setup_script_contents
                        if import_environment.setup_script_contents else "",
                variables=merge_dicts(underlying_environment.variables, import_environment.variables),
                dependencies=merge_dicts(underlying_environment.dependencies, import_environment.dependencies)
                )

    return import_environment
