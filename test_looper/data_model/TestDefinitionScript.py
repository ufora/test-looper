"""
TestScriptDefinition

Models a test-script, and functions for extracting the TestDefinitions from it
"""
import test_looper.core.algebraic as algebraic
import test_looper.core.algebraic_to_json as algebraic_to_json
import test_looper.data_model.TestDefinition as TestDefinition
import yaml
import logging


DefineEnvironment = algebraic.Alternative("DefineEnvironment")
DefineEnvironment.Import = {'import': str}
DefineEnvironment.Group = {'group': algebraic.List(str)}
DefineEnvironment.Environment = TestDefinition.TestEnvironment.Environment

DefineBuild = algebraic.Alternative("DefineBuild")
DefineTest = algebraic.Alternative("DefineTest")
DefineDeployment = algebraic.Alternative("DefineDeployment")

DefineBuild.Build = {
    'command': str,
    'dependencies': algebraic.Dict(str,str),
    'variables': algebraic.Dict(str,str)
    }

DefineTest.Test = {
    'command': str,
    'dependencies': algebraic.Dict(str,str),
    'variables': algebraic.Dict(str,str)
    }

DefineDeployment.Deployment = {
    'command': str,
    'dependencies': algebraic.Dict(str,str),
    'variables': algebraic.Dict(str,str),
    'portExpose': algebraic.Dict(str,int)
    }

TestDefinitionScript = algebraic.Alternative("TestDefinitionScript")
TestDefinitionScript.Definition = {
    "looper_version": int,
    "repos": algebraic.Dict(str,str),
    "environments": algebraic.Dict(str, DefineEnvironment),
    "builds": algebraic.Dict(str, DefineBuild),
    "tests": algebraic.Dict(str, DefineTest),
    "deployments": algebraic.Dict(str, DefineDeployment)
    }

reservedNames = ["data", "source"]

def ensure_graph_closed_and_noncyclic(graph):
    for g in graph:
        for i in graph[g]:
            if i not in graph:
                raise Exception("%s depends on %s for which we have no definition" % (g,i))

    seen = set()
    while len(seen) != len(graph):
        added = False
        for g in graph:
            if not [x for x in graph[g] if x not in seen]:
                seen.add(g)
                added = True
        if not added:
            raise Exception("Builds %s are circular." % str([x for x in graph if x not in seen]))

def extract_tests(testScript):
    repos = {}

    for repoVarName, repoDef in testScript.repos.iteritems():
        if repoVarName in reservedNames:
            raise Exception("%s is a reserved name and can't be used as a reponame." % repoVarName)

        assert len(repoDef.split("/")) == 2, "Improperly formed repo definition: %s" % repoDef

        repoName, commitHash = repoDef.split("/")

        repos[repoVarName] = (repoName, commitHash)

    environments = {}

    for envName, envDef in testScript.environments.iteritems():
        if envDef.matches.Import:
            importText = getattr(envDef,"import")
            assert len(importText.split("/")) == 2, "Invalid import: %s" % importText

            repoName, importEnvName = importText.split("/")

            if repoName not in repos:
                raise Exception("Unknown repo %s" % repoName)

            environments[envName] = TestDefinition.TestEnvironment.Import(
                repo=repos[repoName][0], 
                commitHash=repos[repoName][1],
                name=importEnvName
                )
        elif envDef.matches.Environment:
            environments[envName] = TestDefinition.TestEnvironment.Environment(
                platform=envDef.platform,
                image=envDef.image,
                variables=envDef.variables
                )  

    environmentGroups = {}

    for envName, envDef in testScript.environments.iteritems():
        if envDef.matches.Group:
            environmentGroups[envName] = []

    for envName, envDef in testScript.environments.iteritems():
        if envDef.matches.Group:
            for env in envDef.group:
                if env in environments:
                    environmentGroups[envName].append(env)
                elif env in environmentGroups:
                    raise Exception(
                        "Environment group %s contains reference to %s which is also a group." % 
                            (envName, env)
                        )
                else:
                    raise Exception(
                        "Environment group %s contains reference to %s which is undefined." % 
                            (envName, env)
                        )

    def expand_build_name(name):
        items = name.split("/")
        if len(items) <= 1:
            raise Exception(
                "Invalid build name '%s'. Should be of the form 'name(/subname)*/environment'."
                    % name
                )
        actualName = "/".join(items[:-1])
        envName = items[-1]
        if envName not in environments and envName not in environmentGroups:
            raise Exception("Unknown environment: %s" % envName)

        environments_for_this_build = []
        if envName in environments:
            environments_for_this_build.append(envName)
        else:
            environments_for_this_build.extend(environmentGroups[envName])

        return [actualName + "/" + e for e in environments_for_this_build]

    all_local_build_names = set()

    for name in testScript.builds:
        for real_name in expand_build_name(name):
            all_local_build_names.add(real_name)


    def convert_build_dep(dep,curEnv):
        deps = dep.split("/")

        if deps[0] in repos:
            if len(deps) == 2:
                if deps[1] != "source":
                    raise Exception("Malformed repo dependency: use repo/source or repo/buildname/environment")
                #this is a source dependency
                return TestDefinition.TestDependency.Source(
                    repo=repos[deps[0]][0],
                    commitHash=repos[deps[0]][1],
                    )

            #this is a remote dependency: repoRef/buildName/environment
            if len(deps) < 3:
                raise Exception("Malformed repo dependency: should be of form 'repoReference/buildName/environment'")
            
            env = deps[-1]
            if env == '' or env =='*':
                env = curEnv

            return TestDefinition.TestDependency.ExternalBuild(
                repo=repos[deps[0]][0],
                commitHash=repos[deps[0]][1],
                name="/".join(deps[1:-1]),
                environment=env
                )

        if deps[0] == "data":
            #this is a data dependency
            if len(deps) != 2:
                raise Exception("Malformed data dependency: should be of form 'data/hash'")
            return TestDefinition.TestDependency.Data(shaHash=deps[1])

        env = deps[-1]
        if env == '' or env =='*':
            env = curEnv

        actual_build = "/".join(deps[:-1]) + "/" + env

        if actual_build in all_local_build_names:
            #this is a local dependency: buildName/environment
            if len(deps) < 2:
                raise Exception("Malformed local dependency: should be of form 'buildName/environment'")
            
            return TestDefinition.TestDependency.InternalBuild(
                name="/".join(deps[:-1]),
                environment=env
                )

        raise Exception("Cant find reference to: %s" % actual_build)

    def convert_def(name, d):
        curEnv = name.split("/")[-1]

        if d.matches.Build:
            return TestDefinition.TestDefinition.Build(
                buildCommand=d.command,
                name=name,
                variables=d.variables,
                dependencies={depname: convert_build_dep(dep, curEnv) for (depname, dep) in d.dependencies.items()},
                environment=environments[curEnv]
                )
        if d.matches.Test:
            return TestDefinition.TestDefinition.Test(
                testCommand=d.command,
                name=name,
                variables=d.variables,
                dependencies={depname: convert_build_dep(dep, curEnv) for (depname, dep) in d.dependencies.items()},
                environment=environments[curEnv]
                )
        if d.matches.Deployment:
            return TestDefinition.TestDefinition.Deployment(
                deployCommand=d.command,
                name=name,
                variables=d.variables,
                dependencies={depname: convert_build_dep(dep, curEnv) for (depname, dep) in d.dependencies.items()},
                portExpose=d.portExpose,
                environment=environments[curEnv]
                )

    #a list of things we can actually depend on
    allTests = {}

    all_names_and_defs = (
        list(testScript.builds.items()) 
         + list(testScript.tests.items()) 
         + list(testScript.deployments.items())
        )

    for name, definition in all_names_and_defs:
        if name.split("/")[0] in repos:
            raise Exception("Cant use reuse %s, as it's aready a repo name." % name)

        for actualName in expand_build_name(name):
            allTests[actualName] = convert_def(actualName, definition)

    #ensure our tests don't have circular references
    deps = {}
    for name, definition in allTests.items():
        deps[name] = set([x.name + "/" + x.environment for x in definition.dependencies.values() if x.matches.InternalBuild])

    ensure_graph_closed_and_noncyclic(deps)

    return allTests


def extract_tests_from_str(text):
    json = yaml.load(text)
    
    e = algebraic_to_json.Encoder()

    return extract_tests(e.from_json(json, TestDefinitionScript))
