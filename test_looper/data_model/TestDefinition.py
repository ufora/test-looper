"""
TestDefinition

Objects modeling our tests.
"""
import test_looper.core.algebraic as algebraic
import test_looper.core.GraphUtil as GraphUtil
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
TestDependency.Build = {"repo": str, "name": str, "buildHash": str}
TestDependency.Source = {"repo": str, "commitHash": str, "path": str}

#these are intermediate parse states. Resolved builds won't have them.
TestDependency.InternalBuild = {"name": str}
TestDependency.ExternalBuild = {"repo": str, "commitHash": str, "name": str}

#'repo_name' refers to the name of the repo variable the test definitions (not the git repo). This
#is an intermediate state that's only used mid-parsing while we're resolving references in external
#repos
TestDependency.UnresolvedExternalBuild = {"repo_name": str, "name": str}
TestDependency.UnresolvedSource = {"repo_name": str, "path": str}

EnvironmentReference = algebraic.Alternative("EnvironmentReference")
EnvironmentReference.Reference = {"repo": str, "commitHash": str, "name": str}
EnvironmentReference.UnresolvedReference = {"repo_name": str, "name": str}

RepoReference = algebraic.Alternative("RepoReference")
RepoReference.Import = {"import": str} # /-separated sequence of repo refs
RepoReference.ImportedReference = {"reference": str, "import_source": str, "orig_reference": str}
RepoReference.Reference = {"reference": str}
RepoReference.Pin = {
    "reference": str,
    "branch": str,
    "auto": bool
    }

def RepoReference_reponame(ref):
    return "/".join(ref.reference.split("/")[:-1])
def RepoReference_commitHash(ref):
    return ref.reference.split("/")[-1]
def RepoReference_branchname(ref):
    if ref.matches.Pin:
        return ref.branch
    else:
        return None

RepoReference.reponame = RepoReference_reponame
RepoReference.commitHash = RepoReference_commitHash
RepoReference.branchname = RepoReference_branchname


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
    "cleanupCommand": str,
    'configuration': str,
    'hash': str,
    "name": str,
    "environment_name": str,
    "environment": TestEnvironment,
    "dependencies": algebraic.Dict(str, TestDependency),
    "variables": algebraic.Dict(str,str),
    "disabled": bool, #disabled by default?
    "timeout": int, #max time, in seconds, for the test
    "min_cores": int, #minimum number of cores we should be run on, or zero if we don't care
    "max_cores": int, #maximum number of cores we can take advantage of, or zero
    "min_ram_gb": int, #minimum GB of ram we need to run, or zero if we don't care
    "max_retries": int, #maximum number of times to retry the build
    "retry_wait_seconds": int, #minimum number of seconds to wait before retrying a build
    }
TestDefinition.Test = {
    "testCommand": str,
    "cleanupCommand": str,
    'configuration': str,
    'hash': str,
    "name": str,
    "environment_name": str,
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
    'configuration': str,
    'hash': str,
    "name": str,
    "environment_name": str,
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

def merge(seqs):
    res = []
    i=0
    while True:
        nonemptyseqs = [seq for seq in seqs if seq]
        if not nonemptyseqs: 
            return res

        i+=1
        for seq in nonemptyseqs: # find merge candidates among seq heads
            cand = seq[0]
            nothead=[s for s in nonemptyseqs if cand in s[1:]]
            if nothead: 
                cand=None #reject candidate
            else: 
                break
        if not cand: 
            raise "Inconsistent hierarchy"

        res.append(cand)

        for seq in nonemptyseqs: # remove cand
            if seq[0] == cand: 
                del seq[0]

def method_resolution_order(env, dependencies):
    linearization = [[env]]

    if env.matches.Import:
        for d in env.imports:
            linearization.append(
                method_resolution_order(dependencies[d], dependencies)
                )
        for d in env.imports:
            linearization.append([dependencies[d]])

    return merge(linearization)

def merge_image_and_extra_setup(image, extra_setup):
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
    """Given an 'Import' environment and a dictionary from 
        TestDefinition.EnvironmentReference.Reference -> Environment
    apply the state of the import to the underlying.

    Environments are applied as if they were python classes. All imports
    must descend from 
    """
    assert import_environment.matches.Import

    #check for circularity
    GraphUtil.assertGraphHasNoCycles(
        import_environment, 
        lambda e: [underlying_environments[d] for d in e.imports] if e.matches.Import else ()
        )

    order = method_resolution_order(import_environment, underlying_environments)

    order_by_name = [o.environment_name for o in order]

    assert len([e for e in order if e.matches.Environment]) <= 1, \
        "Can't depend on two different non-import environments: %s" % order_by_name

    environment_name = order[0].environment_name
    inheritance = [e.environment_name for e in order[1:]]
        
    variables = order[-1].variables
    dependencies = order[-1].dependencies

    extra_setups = [e.setup_script_contents for e in order if e.matches.Import and e.setup_script_contents]

    for e in reversed(order[:-1]):
        variables = merge_dicts(variables, e.variables)
        dependencies = merge_dicts(dependencies, e.dependencies)

    if len([e for e in order if e.matches.Environment]) == 1:
        actual_environment = [e for e in order if e.matches.Environment][0]

        platform = actual_environment.platform
        image = actual_environment.image

        if extra_setups:
            image = merge_image_and_extra_setup(actual_environment.image, "\n".join(reversed(extra_setups)))
        
        return TestEnvironment.Environment(
            environment_name=environment_name,
            inheritance=inheritance,
            platform=platform,
            image=image,
            variables=variables,
            dependencies=dependencies
            )
    else:
        return TestEnvironment.Import(
            environment_name=environment_name,
            inheritance=inheritance,
            variables=variables,
            dependencies=dependencies,
            imports=(),
            setup_script_contents="\n".join(reversed(extra_setups))
            )

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
            name=VariableSubstitution.substitute_variables(dep.name, vardefs)
            )
    elif dep.matches.ExternalBuild:
        return TestDependency.ExternalBuild(
            name=VariableSubstitution.substitute_variables(dep.name, vardefs),
            repo=dep.repo,
            commitHash=dep.commitHash
            )
    elif dep.matches.Source:
        return dep
    elif dep.matches.Build:
        return dep
    elif dep.matches.UnresolvedSource:
        return TestDependency.UnresolvedSource(
            repo_name=dep.repo_name,
            path=VariableSubstitution.substitute_variables(dep.path, vardefs)
            )
    elif dep.matches.UnresolvedExternalBuild:
        return TestDependency.UnresolvedExternalBuild(
            repo_name=dep.repo_name,
            name=VariableSubstitution.substitute_variables(dep.name, vardefs)
            )
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
            configuration=test.configuration,
            environment_name=test.environment_name,
            variables=vardefs,
            dependencies=dependencies,
            timeout=test.timeout,
            min_cores=test.min_cores,
            max_cores=test.max_cores,
            min_ram_gb=test.min_ram_gb,
            hash=test.hash,
            **kwargs
            )

    if test.matches.Build:
        return make(
            TestDefinition.Build,
            buildCommand=VariableSubstitution.substitute_variables(test.buildCommand, vardefs),
            cleanupCommand=VariableSubstitution.substitute_variables(test.cleanupCommand, vardefs),
            max_retries=test.max_retries,
            retry_wait_seconds=test.retry_wait_seconds,
            disabled=test.disabled
            )
    elif test.matches.Test:
        return make(
            TestDefinition.Test,
            testCommand=VariableSubstitution.substitute_variables(test.testCommand, vardefs),
            cleanupCommand=VariableSubstitution.substitute_variables(test.cleanupCommand, vardefs),
            disabled=test.disabled
            )
    elif test.matches.Deployment:
        return make(
            TestDefinition.Deployment,
            deployCommand=VariableSubstitution.substitute_variables(test.deployCommand, vardefs),
            portExpose=test.portExpose
            )
