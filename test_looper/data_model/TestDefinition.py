"""
TestDefinition

Objects modeling our tests.
"""
import test_looper.core.algebraic as algebraic
import test_looper.core.algebraic_to_json as algebraic_to_json
import logging
import re
import test_looper.data_model.VariableSubstitution as VariableSubstitution

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

RepoReference = algebraic.Alternative("RepoReference")
RepoReference.Reference = {"reference": str}
RepoReference.Pin = {
    "reference": str,
    "branch": str
    }

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

    if image.matches.AMI:
        return Image.AMI(
            base_ami=image.base_ami, 
            setup_script_contents=image.setup_script_contents + "\n" + extra_setup
            )
    elif image.matches.DockerfileInline:
        return Image.DockerfileInline(
            dockerfile_contents=image.dockerfile_contents + "\n" + extra_setup
            )
    else:
        assert image.matches.AMI, "Can only add setup-script contents to an AMI image, not %s, and we were given %s" % (image, repr(extra_setup))

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

def substitute_variables_in_image(image, vars):
    if image.matches.AMI:
        return Image.AMI(
            base_ami=image.base_ami, 
            setup_script_contents=VariableSubstitution.substitute_variables(image.setup_script_contents, vars)
            )
    else:
        return image


def apply_substitutions_to_dependency(dep, vardefs):
    if dep.matches.InternalBuild:
        return TestDependency.InternalBuild(
            name=VariableSubstitution.substitute_variables(dep.name, vardefs),
            environment=VariableSubstitution.substitute_variables(dep.environment, vardefs)
            )
    elif dep.matches.ExternalBuild:
        return TestDependency.ExternalBuild(
            name=VariableSubstitution.substitute_variables(dep.name, vardefs),
            environment=VariableSubstitution.substitute_variables(dep.environment, vardefs),
            repo=dep.repo,
            commitHash=dep.commitHash
            )
    elif dep.matches.Source:
        return dep
    else:
        assert False, "Unknown dep type: %s" % dep

def apply_substitutions_to_dependencies(deps, vardefs):
    return {VariableSubstitution.substitute_variables(k, vardefs):
                        apply_substitutions_to_dependency(v, vardefs)
                    for k,v in deps.iteritems()}

def apply_environment_substitutions(env):
    """Apply replacement logic to variable definitions in an environment.

    This is the final step when producing an environment. Every variable,
    ami setup script, and dependency target-location can reference environment
    variables as "${VAR}".

    Only variables that are valid identifiers (letters, numbers, - and _) will 
    have their state applied. We detect cycles and throw an exception if we get one, 
    and we don't substitute variables in the names of variables.
    """
    vardefs = VariableSubstitution.apply_variable_substitutions_and_merge_repeatedly(env.variables)

    deps = apply_substitutions_to_dependencies(env.dependencies, vardefs)

    for d in deps:
        assert "$" not in d, "Environment %s produced malformed dependency %s" % (env.environment_name, d)

    if env.matches.Environment:
        return TestEnvironment.Environment(
                environment_name=env.environment_name,
                inheritance=env.inheritance,
                platform=env.platform,
                image=substitute_variables_in_image(env.image, vardefs),
                variables=vardefs,
                dependencies=deps
                )
    else:
        return TestEnvironment.Import(
            environment_name=env.environment_name,
            inheritance=env.inheritance,
            imports=env.imports,
            setup_script_contents=VariableSubstitution.substitute_variables(env.setup_script_contents, vardefs),
            variables=vardefs,
            dependencies=deps
            )

def apply_test_substitutions(test, env, input_var_defs):
    vardefs = dict(env.variables)
    vardefs.update(test.variables)

    vardefs = VariableSubstitution.apply_variable_substitutions_and_merge_repeatedly(vardefs)

    #dependencies need to resolve without use of 'input_var_defs'
    dependencies = apply_substitutions_to_dependencies(test.dependencies, vardefs)

    #now allow the input vars to apply
    vardefs = VariableSubstitution.apply_variable_substitutions_and_merge_repeatedly(vardefs, input_var_defs)

    def make(type, **kwargs):
        return type(
            name=test.name,
            environment=env,
            variables=vardefs,
            dependencies=dependencies,
            disabled=test.disabled,
            timeout=test.timeout,
            min_cores=test.min_cores,
            max_cores=test.max_cores,
            min_ram_gb=test.min_ram_gb,
            **kwargs
            )

    if test.matches.Build:
        return make(
            TestDefinition.Build,
            buildCommand=VariableSubstitution.substitute_variables(test.buildCommand, vardefs)
            )
    elif test.matches.Test:
        return make(
            TestDefinition.Test,
            testCommand=VariableSubstitution.substitute_variables(test.testCommand, vardefs),
            cleanupCommand=VariableSubstitution.substitute_variables(test.cleanupCommand, vardefs)
            )
    elif test.matches.Deployment:
        return make(
            TestDefinition.Deployment,
            deployCommand=VariableSubstitution.substitute_variables(test.deployCommand, vardefs),
            portExpose=test.portExpose
            )
