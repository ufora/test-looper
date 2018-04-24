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
TestDependency.Build = {"name": str, "buildHash": str, "artifact": str }
TestDependency.Source = {"repo": str, "commitHash": str, "path": str }

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

Stage = algebraic.Alternative("Stage")
Artifact = algebraic.Alternative("Artifact")
Stage.Stage = {
    "command": str, #command to run
    "cleanup": str, #command to copy output to build output directory
    "order": float,
    "artifacts": algebraic.List(Artifact)
    }

ArtifactFormat = algebraic.Alternative("ArtifactFormat")
ArtifactFormat.Tar = {}
ArtifactFormat.Zip = {}
ArtifactFormat.Files = {}
ArtifactFormat.setCreateDefault(lambda: ArtifactFormat.Tar())


Artifact.Artifact = {
    "name": str, #unique within the entire build. If populated, then this output is named "build_name/artifact_name"
                 #if blank, then the output is just named "build_name". Names must be unique. A blank name is only
                 #allowed if we have exactly one build artifact across the stages
    "directory": str, #directory from which to build the actual build artifact
    "include_patterns": algebraic.List(str), #list of globs we want to include. If empty, then we include everything.
    "exclude_patterns": algebraic.List(str), #list of globs we want to exclude from the build
    "format": ArtifactFormat
    }

RepoReference = algebraic.Alternative("RepoReference")
RepoReference.Import = {"import": str} # /-separated sequence of repo refs
RepoReference.ImportedReference = {"reference": str, "import_source": str, "orig_reference": str}
RepoReference.Reference = {"reference": str}
RepoReference.Pin = {
    "reference": str,
    "branch": str,
    "auto": bool,
            
    #if we update this commit and the branch is prioritized, do we want the commit prioritized also?
    "prioritize": bool
    }

def RepoReference_reponame(ref):
    if ref.matches.Import:
        return None
    return "/".join(ref.reference.split("/")[:-1])
def RepoReference_commitHash(ref):
    if ref.matches.Import:
        return None
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
TestEnvironment.Unresolved = {}
TestEnvironment.Environment = {
    "environment_name": str,
    "inheritance": algebraic.List(str),
    "platform": Platform,
    "image": Image,
    "variables": algebraic.Dict(str, str),
    "dependencies": algebraic.Dict(str, TestDependency),
    "test_configuration": str,
    'test_stages': algebraic.List(Stage),
    "test_timeout": int,
    "test_min_cores": int,
    "test_max_cores": int,
    "test_min_ram_gb": int,
    "test_min_disk_gb": int,
    "test_max_retries": int,
    "test_retry_wait_seconds": int
    }

TestEnvironment.Import = {
    "environment_name": str,
    "inheritance": algebraic.List(str),
    "imports": algebraic.List(EnvironmentReference),
    "setup_script_contents": str,
    "variables": algebraic.Dict(str, str),
    "dependencies": algebraic.Dict(str, TestDependency),
    "test_configuration": str,
    'test_stages': algebraic.List(Stage),
    "test_timeout": int,
    "test_min_cores": int,
    "test_max_cores": int,
    "test_min_ram_gb": int,
    "test_min_disk_gb": int,
    "test_max_retries": int,
    "test_retry_wait_seconds": int
    }

TestDefinition = algebraic.Alternative("TestDefinition")
TestDefinition.Build = {
    'stages': algebraic.List(Stage),
    'configuration': str,
    'project': str,
    'hash': str,
    "name": str,
    "environment_name": str,
    "environment_mixins": algebraic.List(str),
    "environment": TestEnvironment,
    "dependencies": algebraic.Dict(str, TestDependency),
    "variables": algebraic.Dict(str,str),
    "disabled": bool, #disabled by default?
    "timeout": int, #max time, in seconds, for the test
    "min_cores": int, #minimum number of cores we should be run on, or zero if we don't care
    "max_cores": int, #maximum number of cores we can take advantage of, or zero
    "min_ram_gb": int, #minimum GB of ram we need to run, or zero if we don't care
    "min_disk_gb": int, #minimum GB of disk space we need for this test
    "max_retries": int, #maximum number of times to retry the build
    "retry_wait_seconds": int, #minimum number of seconds to wait before retrying a build
    }
TestDefinition.Test = {
    'stages': algebraic.List(Stage),
    'configuration': str,
    'project': str,
    'hash': str,
    "name": str,
    "environment_name": str,
    "environment_mixins": algebraic.List(str),
    "environment": TestEnvironment,
    "dependencies": algebraic.Dict(str, TestDependency),
    "variables": algebraic.Dict(str,str),
    "disabled": bool, #disabled by default?
    "timeout": int, #max time, in seconds, for the test
    "min_cores": int, #minimum number of cores we should be run on, or zero if we don't care
    "max_cores": int, #maximum number of cores we can take advantage of, or zero
    "min_ram_gb": int, #minimum GB of ram we need to run, or zero if we don't care
    "min_disk_gb": int, #minimum GB of disk space we need
    }
TestDefinition.Deployment = {
    'configuration': str,
    'project': str,
    'hash': str,
    "name": str,
    "environment_name": str,
    "environment_mixins": algebraic.List(str),
    "environment": TestEnvironment,
    "dependencies": algebraic.Dict(str, TestDependency),
    "variables": algebraic.Dict(str,str),
    'portExpose': algebraic.Dict(str,int),
    "timeout": int, #max time, in seconds, for the test
    "min_cores": int, #minimum number of cores we should be run on, or zero if we don't care
    "max_cores": int, #maximum number of cores we can take advantage of, or zero
    "min_ram_gb": int, #minimum GB of ram we need to run, or zero if we don't care
    "min_disk_gb": int, #minimum GB of ram we need to run, or zero if we don't care
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
        for d in reversed(env.imports):
            linearization.append(
                method_resolution_order(dependencies[d], dependencies)
                )
        for d in reversed(env.imports):
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

def makeDict(**kwargs):
    return dict(kwargs)

def pickFirstNonzero(list, defaultVal=0):
    for l in list:
        if l:
            return l
    return defaultVal

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

    commonKwargs = makeDict(
        test_configuration=pickFirstNonzero([e.test_configuration for e in order], ""),
        test_stages=sum([list(e.test_stages) for e in reversed(order)], []),
        test_timeout=pickFirstNonzero([e.test_timeout for e in order]),
        test_min_cores=pickFirstNonzero([e.test_min_cores for e in order]),
        test_max_cores=pickFirstNonzero([e.test_max_cores for e in order]),
        test_min_ram_gb=pickFirstNonzero([e.test_min_ram_gb for e in order]),
        test_min_disk_gb=pickFirstNonzero([e.test_min_disk_gb for e in order]),
        test_max_retries=pickFirstNonzero([e.test_max_retries for e in order]),
        test_retry_wait_seconds=pickFirstNonzero([e.test_retry_wait_seconds for e in order])
        )

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
            dependencies=dependencies,
            **commonKwargs
            )
    else:
        return TestEnvironment.Import(
            environment_name=environment_name,
            inheritance=inheritance,
            variables=variables,
            dependencies=dependencies,
            imports=(),
            setup_script_contents="\n".join(reversed(extra_setups)),
            **commonKwargs
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
        return env._withReplacement(
                image=substitute_variables_in_image(env.image, vardefs),
                variables=vardefs,
                dependencies=deps
                )
    else:
        return env._withReplacement(
            setup_script_contents=VariableSubstitution.substitute_variables(env.setup_script_contents, vardefs),
            variables=vardefs,
            dependencies=deps
            )

def apply_variable_substitution_to_artifact(artifact, vardefs):
    return Artifact.Artifact(
        name=VariableSubstitution.substitute_variables(artifact.name, vardefs),
        directory=VariableSubstitution.substitute_variables(artifact.directory, vardefs),
        include_patterns=[VariableSubstitution.substitute_variables(i, vardefs) for i in artifact.include_patterns],
        exclude_patterns=[VariableSubstitution.substitute_variables(i, vardefs) for i in artifact.exclude_patterns],
        format=artifact.format
        )

def apply_variable_substitutions_to_stage(stage, vardefs):
    return Stage.Stage(
        command=VariableSubstitution.substitute_variables(stage.command, vardefs),
        cleanup=VariableSubstitution.substitute_variables(stage.cleanup, vardefs),
        order=stage.order,
        artifacts=[apply_variable_substitution_to_artifact(a, vardefs) for a in stage.artifacts],
        )

def apply_variable_substitution_to_stages(stages, vardefs):
    return [apply_variable_substitutions_to_stage(s, vardefs) for s in stages]

def apply_environment_to_test(test, env, input_var_defs):
    vardefs = dict(env.variables)
    vardefs.update(test.variables)

    vardefs = VariableSubstitution.apply_variable_substitutions_and_merge_repeatedly(vardefs)

    #dependencies need to resolve without use of 'input_var_defs'
    dependencies = apply_substitutions_to_dependencies(test.dependencies, vardefs)

    #now allow the input vars to apply
    vardefs = VariableSubstitution.apply_variable_substitutions_and_merge_repeatedly(vardefs, input_var_defs)

    if test.configuration:
        config = test.configuration
    else:
        config = env.test_configuration

    if not config:
        config = test.environment_name

    if test.project:
        project = test.project
    else:
        project = test.name.split("/")[0]

    def make(type, **kwargs):
        return type(
            name=test.name,
            environment=env,
            configuration=VariableSubstitution.substitute_variables(config, vardefs),
            project=VariableSubstitution.substitute_variables(project, vardefs),
            environment_name=test.environment_name,
            environment_mixins=test.environment_mixins,
            variables=vardefs,
            dependencies=dependencies,
            timeout=test.timeout or env.test_timeout,
            min_cores=test.min_cores or env.test_min_cores,
            max_cores=test.max_cores or env.test_max_cores,
            min_ram_gb=test.min_ram_gb or env.test_min_ram_gb,
            min_disk_gb=test.min_disk_gb or env.test_min_disk_gb,
            hash=test.hash,
            **kwargs
            )

    if test.matches.Build or test.matches.Test:
        stages = env.test_stages + test.stages
        stages = apply_variable_substitution_to_stages(stages, vardefs)

    if test.matches.Build:
        return make(
            TestDefinition.Build,
            stages=stages,
            max_retries=test.max_retries,
            retry_wait_seconds=test.retry_wait_seconds,
            disabled=test.disabled
            )
    elif test.matches.Test:
        return make(
            TestDefinition.Test,
            stages=stages,
            disabled=test.disabled
            )
    elif test.matches.Deployment:
        return make(
            TestDefinition.Deployment,
            portExpose=test.portExpose
            )

def apply_variable_substitution_to_test(test, input_var_defs):
    vardefs = dict(test.variables)

    vardefs = VariableSubstitution.apply_variable_substitutions_and_merge_repeatedly(vardefs)

    #dependencies need to resolve without use of 'input_var_defs'
    dependencies = apply_substitutions_to_dependencies(test.dependencies, vardefs)

    #now allow the input vars to apply
    vardefs = VariableSubstitution.apply_variable_substitutions_and_merge_repeatedly(vardefs, input_var_defs)

    if test.matches.Build:
        return test._withReplacement(
            variables=vardefs,
            stages=apply_variable_substitution_to_stages(test.stages, vardefs)
            )
    elif test.matches.Test:
        return test._withReplacement(
            variables=vardefs,
            stages=apply_variable_substitution_to_stages(test.stages, vardefs)
            )
    elif test.matches.Deployment:
        return test._withReplacement(
            variables=vardefs
            )
