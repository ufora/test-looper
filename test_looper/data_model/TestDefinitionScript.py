"""
TestDefinitionScript

Models a test-script, and functions for extracting the TestDefinitions from it
"""
import test_looper.core.algebraic as algebraic
import test_looper.core.algebraic_to_json as algebraic_to_json
import test_looper.data_model.TestDefinition as TestDefinition

import yaml
import json
import simplejson 
import logging

VALID_VERSIONS = [2,3]

Platform = TestDefinition.Platform

VariableDict = algebraic.Dict(str, str)

Image = algebraic.Alternative("Image")
Image.DockerfileInline = {"dockerfile_contents": str}
Image.Dockerfile = {"dockerfile": str}
Image.AMI = {"base_ami": str, "setup_script_contents": str}

DefineEnvironment = algebraic.Alternative("DefineEnvironment")
DefineEnvironment.Import = {
    'base': algebraic.List(str),
    'setup_script_contents': str,
    "variables": VariableDict,
    "dependencies": algebraic.Dict(str, str),
    "test_configuration": str,
    "test_preCommand": str,
    "test_preCleanupCommand": str,
    "test_timeout": int,
    "test_min_cores": int,
    "test_max_cores": int,
    "test_min_ram_gb": int,
    "test_max_retries": int,
    "test_retry_wait_seconds": int
    }

DefineEnvironment.Environment = {
    "platform": Platform,
    "image": Image,
    "variables": VariableDict,
    "dependencies": algebraic.Dict(str, str),
    "test_configuration": str,
    "test_preCommand": str,
    "test_preCleanupCommand": str,
    "test_timeout": int,
    "test_min_cores": int,
    "test_max_cores": int,
    "test_min_ram_gb": int,
    "test_max_retries": int,
    "test_retry_wait_seconds": int
    }

DefineBuild = algebraic.Alternative("DefineBuild")
DefineTest = algebraic.Alternative("DefineTest")
DefineDeployment = algebraic.Alternative("DefineDeployment")

DefineBuild.Build = {
    'command': str,
    'environment': str,
    'mixins': algebraic.List(str), #environments to 'mix in' to modify the behavior of the test
    'configuration': str,
    'project': str,
    'cleanup': str, #command to run to copy test outputs to relevant directories...
    'dependencies': algebraic.Dict(str,str),
    'variables': VariableDict,
    "timeout": int, #max time, in seconds, for the test
    "disabled": bool, #disabled by default?
    "min_cores": int, #minimum number of cores we should be run on, or zero if we don't care
    "max_cores": int, #maximum number of cores we can take advantage of, or zero
    "min_ram_gb": int, #minimum GB of ram we need to run, or zero if we don't care
    "max_retries": int, #maximum number of times to retry the build
    "retry_wait_seconds": int, #minimum number of seconds to wait before retrying a build
    }

DefineTest.Test = {
    'command': str,
    'environment': str,
    'mixins': algebraic.List(str), #environments to 'mix in' to modify the behavior of the test
    'configuration': str,
    'project': str,
    'cleanup': str, #command to run to copy test outputs to relevant directories...
    'dependencies': algebraic.Dict(str,str),
    'variables': VariableDict,
    "disabled": bool, #disabled by default?
    "timeout": int, #max time, in seconds, for the test
    "min_cores": int, #minimum number of cores we should be run on, or zero if we don't care
    "max_cores": int, #maximum number of cores we can take advantage of, or zero
    "min_ram_gb": int, #minimum GB of ram we need to run, or zero if we don't care
    }

DefineDeployment.Deployment = {
    'command': str,
    'environment': str,
    'mixins': algebraic.List(str), #environments to 'mix in' to modify the behavior of the test
    'configuration': str,
    'project': str,
    'dependencies': algebraic.Dict(str,str),
    'variables': VariableDict,
    'portExpose': algebraic.Dict(str,int),
    "timeout": int, #max time, in seconds, for the test
    "min_cores": int, #minimum number of cores we should be run on, or zero if we don't care
    "max_cores": int, #maximum number of cores we can take advantage of, or zero
    "min_ram_gb": int, #minimum GB of ram we need to run, or zero if we don't care
    }

RepoReference = TestDefinition.RepoReference

DefineInclude = algebraic.Alternative("DefineInclude")
DefineInclude.Include = {"path": str, "variables": algebraic.Dict(str, str)}

TestDefinitionScript = algebraic.Alternative("TestDefinitionScript")
TestDefinitionScript.Definition = {
    "looper_version": int,
    "repos": algebraic.Dict(str,RepoReference),
    "includes": algebraic.List(DefineInclude),
    "environments": algebraic.Dict(str, DefineEnvironment),
    "builds": algebraic.Dict(str, DefineBuild),
    "tests": algebraic.Dict(str, DefineTest),
    "deployments": algebraic.Dict(str, DefineDeployment)
    }

reservedNames = ["data", "source", "HEAD"]

def map_image(reponame, commitHash, image_def):
    if image_def.matches.Dockerfile:
        return TestDefinition.Image.Dockerfile(
            dockerfile=image_def.dockerfile,
            repo=reponame,
            commitHash=commitHash
            )
    if image_def.matches.DockerfileInline:
        return TestDefinition.Image.DockerfileInline(
            dockerfile_contents=image_def.dockerfile_contents
            )
    elif image_def.matches.AMI:
        return TestDefinition.Image.AMI(
            base_ami=image_def.base_ami,
            setup_script_contents=image_def.setup_script_contents,
            )
    else:
        assert False, "Can't convert this kind of image: %s" % image_def

def extract_tests(curRepoName, curCommitHash, testScript, version, externally_defined_repos=None):
    for repoVarName, repoPin in testScript.repos.iteritems():
        if repoVarName in reservedNames:
            raise Exception("%s is a reserved name and can't be used as a reponame." % repoVarName)

        if repoPin.matches.Reference or repoPin.matches.Pin or repoPin.matches.ImportedReference:
            repoDef = repoPin.reference

            assert len(repoDef.split("/")) >= 2, "Improperly formed repo definition: %s" % repoDef

            parts = repoDef.split("/")

            assert len(parts) >= 2, "Improperly formed repo definition: %s" % repoDef

            repoName = "/".join(parts[:-1])
            commitHash = parts[-1]

            if commitHash == "":
                raise Exception("Can't have an empty commitHash")

    all_repos = set(externally_defined_repos) if externally_defined_repos else set()
    for reponame in testScript.repos:
        all_repos.add(reponame)

    environments = {}

    def map_environment_dep(dep):
        deps = dep.split("/")

        if "$" in deps[0]:
            raise Exception("Invalid dependency: " + dep + 
                    ". First part of dependencies can't have a substitution. " + 
                    "Use 'HEAD' as a prefix if you need to refer to a build in the current commit.")

        if deps and deps[0] == "HEAD":
            if len(deps) == 1 or deps[1] == "source":
                #this is a source dependency
                return TestDefinition.TestDependency.Source(
                    repo=curRepoName, 
                    commitHash=curCommitHash,
                    path="/".join(deps[2:])
                    )

            if len(deps) < 3:
                raise Exception("Malformed repo dependency: should be of form 'repoReference/buildName/environment'")
            
            return TestDefinition.TestDependency.InternalBuild(
                name="/".join(deps[1:])
                )
        else:
            if deps[0] not in all_repos:
                raise Exception("Environment dependencies must reference a named repo. Can't find %s for %s" % (deps[0], dep))

            if len(deps) == 1 or deps[1] == "source":
                #this is a source dependency
                return TestDefinition.TestDependency.UnresolvedSource(
                    repo_name=deps[0],
                    path="/".join(deps[2:])
                    )

            if len(deps) < 3:
                raise Exception("Malformed repo dependency: should be of form 'repoReference/buildName/environment'")
            
            return TestDefinition.TestDependency.UnresolvedExternalBuild(
                repo_name=deps[0],
                name="/".join(deps[1:])
                )

    def parseEnvironment(envName, parents=()):
        if parents and parents[-1] in parents[:-1]:
            raise Exception("Circular environment dependencies: %s" % (parents,))

        if envName in environments:
            return environments[envName]

        envDef = testScript.environments[envName]

        if envDef.matches.Import:
            imports = []

            for import_string in envDef.base:
                import_parts = import_string.split("/")

                if len(import_parts) == 1:
                    #this is a local import
                    if import_parts[0] not in testScript.environments:
                        raise Exception("Unknown environment '%s'" % import_parts[0])

                    imports.append(
                        TestDefinition.EnvironmentReference(
                            repo=curRepoName,
                            commitHash=curCommitHash,
                            name=import_parts[0]
                            )
                        )
                else:
                    assert len(import_parts) == 2, "Invalid import: %s" % import_string

                    repoName, importEnvName = import_parts

                    imports.append(
                        TestDefinition.EnvironmentReference.UnresolvedReference(
                            repo_name=repoName,
                            name=importEnvName
                            )
                        )

            import_env = TestDefinition.TestEnvironment.Import(
                environment_name=envName,
                inheritance=(),
                imports=tuple(imports),
                setup_script_contents=envDef.setup_script_contents,
                variables=envDef.variables,
                dependencies={
                    "test_inputs/" + name: map_environment_dep(dep) for name, dep in envDef.dependencies.iteritems()
                    },
                test_configuration=envDef.test_configuration,
                test_preCommand=envDef.test_preCommand,
                test_preCleanupCommand=envDef.test_preCleanupCommand,
                test_timeout=envDef.test_timeout,
                test_min_cores=envDef.test_min_cores,
                test_max_cores=envDef.test_max_cores,
                test_min_ram_gb=envDef.test_min_ram_gb,
                test_max_retries=envDef.test_max_retries,
                test_retry_wait_seconds=envDef.test_retry_wait_seconds
                )

            environments[envName] = import_env
                    
        elif envDef.matches.Environment:
            environments[envName] = TestDefinition.TestEnvironment.Environment(
                environment_name=envName,
                inheritance=(),
                platform=envDef.platform,
                image=map_image(curRepoName, curCommitHash, envDef.image),
                variables=envDef.variables,
                dependencies={
                    "test_inputs/" + name: map_environment_dep(dep) for name, dep in envDef.dependencies.iteritems()
                    },
                test_configuration=envDef.test_configuration,
                test_preCommand=envDef.test_preCommand,
                test_preCleanupCommand=envDef.test_preCleanupCommand,
                test_timeout=envDef.test_timeout,
                test_min_cores=envDef.test_min_cores,
                test_max_cores=envDef.test_max_cores,
                test_min_ram_gb=envDef.test_min_ram_gb,
                test_max_retries=envDef.test_max_retries,
                test_retry_wait_seconds=envDef.test_retry_wait_seconds
                )  

        return environments[envName]

    environments = {}

    for envName, envDef in testScript.environments.iteritems():
        environments[envName] = parseEnvironment(envName)

    def convert_build_dep(dep,curEnv):
        deps = dep.split("/")

        if "$" in deps[0]:
            raise Exception("Invalid dependency: " + dep + 
                    ". First part of dependencies can't have a substitution. " + 
                    "Use 'HEAD' as a prefix if you need to refer to a build in the current commit.")
        if deps and deps[0] == "HEAD":
            if len(deps) == 1 or deps[1] == "source":
                return TestDefinition.TestDependency.Source(
                    repo=curRepoName, 
                    commitHash=curCommitHash,
                    path="/".join(deps[2:])
                    )
            else:
                return TestDefinition.TestDependency.InternalBuild(
                    name="/".join(deps[1:])
                    )
        else:
            if deps[0] in all_repos:
                if len(deps) == 1 or deps[1] == "source":
                    #this is a source dependency
                    return TestDefinition.TestDependency.UnresolvedSource(
                        repo_name=deps[0],
                        path="/".join(deps[2:])
                        )

                #this is a remote dependency: repoRef/buildName/environment
                if len(deps) < 3:
                    raise Exception("Malformed repo dependency: should be of form 'repoReference/buildName/environment'")
                
                return TestDefinition.TestDependency.UnresolvedExternalBuild(
                    repo_name=deps[0],
                    name="/".join(deps[1:])
                    )
            
            return TestDefinition.TestDependency.InternalBuild(
                name="/".join(deps)
                )

    def convert_def(name, d):
        curEnv = d.environment or name.split("/")[-1]

        assert "$" not in curEnv, "Malformed name %s" % name

        deps = {"test_inputs/" + depname: convert_build_dep(dep, curEnv) for (depname, dep) in d.dependencies.items()}

        if version == 2:
            deps["src"] = TestDefinition.TestDependency.Source(repo=curRepoName, commitHash=curCommitHash, path="")

        if d.matches.Build:
            return TestDefinition.TestDefinition.Build(
                buildCommand=d.command,
                cleanupCommand=d.cleanup,
                configuration=d.configuration,
                project=d.project,
                name=name,
                variables=d.variables,
                dependencies=deps,
                environment_name=curEnv,
                environment_mixins=[x for x in d.mixins if x],
                environment=TestDefinition.TestEnvironment.Unresolved(),
                timeout=d.timeout,
                disabled=d.disabled,
                min_cores=d.min_cores,
                max_cores=d.max_cores,
                min_ram_gb=d.min_ram_gb,
                max_retries=d.max_retries,
                retry_wait_seconds=d.retry_wait_seconds,
                hash=""
                )
        if d.matches.Test:
            return TestDefinition.TestDefinition.Test(
                testCommand=d.command,
                cleanupCommand=d.cleanup,
                configuration=d.configuration,
                project=d.project,
                name=name,
                variables=d.variables,
                dependencies=deps,
                disabled=d.disabled,
                environment_name=curEnv,
                environment_mixins=[x for x in d.mixins if x],
                environment=TestDefinition.TestEnvironment.Unresolved(),
                timeout=d.timeout,
                min_cores=d.min_cores,
                max_cores=d.max_cores,
                min_ram_gb=d.min_ram_gb,
                hash=""
                )
        if d.matches.Deployment:
            return TestDefinition.TestDefinition.Deployment(
                deployCommand=d.command,
                configuration=d.configuration,
                project=d.project,
                name=name,
                variables=d.variables,
                dependencies=deps,
                portExpose=d.portExpose,
                environment_name=curEnv,
                environment_mixins=[x for x in d.mixins if x],
                environment=TestDefinition.TestEnvironment.Unresolved(),
                timeout=d.timeout,
                min_cores=d.min_cores,
                max_cores=d.max_cores,
                min_ram_gb=d.min_ram_gb,
                hash=""
                )

    #a list of things we can actually depend on
    allTests = {}

    all_names_and_defs = (
        list(testScript.builds.items()) 
         + list(testScript.tests.items()) 
         + list(testScript.deployments.items())
        )

    for name, definition in all_names_and_defs:
        if name.split("/")[0] in all_repos:
            raise Exception("Cant produce a test with name %s, because %s is already a repo name." % (name, name.split("/")[0]))

        allTests[name] = convert_def(name, definition)

    return allTests, environments, testScript.repos, testScript.includes

def flatten(l):
    if not isinstance(l, list):
        return [l]
    res = []
    for i in l:
        res.extend(flatten(i))
    return res

def dictionary_cross_product(kv_pairs):
    if len(kv_pairs) == 0:
        return [{}]

    subs = dictionary_cross_product(kv_pairs[1:])

    key, values = kv_pairs[0]

    result = []
    for v in values:
        for s in subs:
            s = dict(s)
            s[key] = v
            result.append(s)

    return result

class MacroExpander(object):
    def expand_macros(self, json, variables, isVarDef=False):
        if isinstance(json, unicode):
            json = str(json)
        if isinstance(json, str):
            if variables:
                for k,v in sorted(variables.iteritems()):
                    if isinstance(v, (int, bool, float)):
                        v = str(v)
                    if isinstance(v, str):
                        json = json.replace("${" + k + "}", v)
                    else:
                        if "${" + k + "}" in json:
                            if json == "${" + k + "}":
                                return v
                            else:
                                raise Exception("Can't replace in-string variable '%s' with non-string value %s" % (k,v))
            return json
        
        if isinstance(json, list):
            return [self.expand_macros(x, variables, isVarDef) for x in json]
        
        if isinstance(json, dict):
            if sorted(json.keys()) == ["define", "in"]:
                to_use = dict(variables)
                for k,v in self.expand_macros(json["define"], variables, True).iteritems():
                    if k in to_use:
                        raise Exception("Can't redefine variable %s" % k)
                    to_use[k] = v
                return self.expand_macros(json["in"], to_use, isVarDef)
            
            if sorted(json.keys()) == ["over", "squash"]:
                squashover = self.expand_macros(json["squash"], variables, True)

                if not isinstance(squashover, dict):
                    raise Exception("Can't squash %s into subitems because its not a dict" % squashover)

                def squash(child):
                    if not isinstance(child, dict):
                        raise Exception("Can't squash %s into %s because it's not a dict" % (squashover, child))

                    child = dict(child)

                    for k,v in squashover.iteritems():
                        if k in child:
                            raise Exception("Can't define %s twice when squashing %s into %s" % (k, squashover, child))
                        child[k] = v

                    return child

                children = flatten(self.expand_macros(json["over"], variables, isVarDef))
                if isinstance(children, dict):
                    return squash(children)

                if isinstance(children, list):
                    return [squash(c) for c in children]

                raise Exception("Arguments to squash need to be dicts, not %s" % children)

            if sorted(json.keys()) == ["merge"]:
                to_merge = json["merge"]

                assert isinstance(to_merge, list), "Can't apply a merge operation to %s because its not a list" % to_merge
                
                to_merge = [self.expand_macros(i, variables, isVarDef) for i in to_merge]

                assert to_merge, "Can't apply a merge operation to %s because its empty" % to_merge

                if isinstance(to_merge[0], list):
                    res = []
                    for to_append in to_merge:
                        if not isinstance(to_append, list):
                            raise Exception("Can't apply a merge operation to %s because not all children are lists." % to_merge)
                        res.extend(to_append)
                    return res
                if isinstance(to_merge[0], dict):
                    res = {}
                    for to_append in to_merge:
                        if not isinstance(to_append, dict):
                            raise Exception("Can't apply a merge operation to %s because not all children are dictionaries." % to_merge)
                        for k,v in to_append.iteritems():
                            if k in res:
                                raise Exception("merging %s produced key %s twice" % (to_merge, k))
                            res[k] = v
                    return res

                raise Exception("Can't merge %s - all children need to be either dictionaries or lists" % to_merge)

            if sorted(json.keys()) == ["foreach", "repeat"]:
                assert isinstance(json['repeat'], (dict,list)), "Can't repeat %s because it's not a dictionary or list" % json['repeat']

                repeat_over = self.expand_macros(json['foreach'], variables, True)

                if isinstance(repeat_over, dict):
                    #take the cross product of all elements of the dictionary
                    #e.g.
                    #A: [1,2,3]
                    #B: [1,2,3]
                    #will produce 9 items
                    items = dictionary_cross_product(repeat_over.items())
                else:
                    items = flatten(repeat_over)

                res = None
                for sub_replacements in items:
                    to_use = dict(variables)

                    for k,v in sub_replacements.iteritems():
                        if k in to_use:
                            raise Exception("Can't redefine variable %s" % k)
                        to_use[k] = v

                    expanded = self.expand_macros(json['repeat'], to_use, isVarDef)

                    if isinstance(expanded, dict):
                        if res is None:
                            res = {}

                        for k,v in expanded.iteritems():
                            if k in res:
                                raise Exception("Can't define %s twice" % k)
                            res[k] = v
                    elif isinstance(expanded, list):
                        if res is None:
                            res = []

                        res.extend(expanded)
                return res
            return {self.expand_macros(k,variables, isVarDef): self.expand_macros(v, variables, isVarDef) for k,v in json.iteritems()}

        return json

class IncludesMacroExpander(MacroExpander):
    def expand_macros(self, json, variables, isVarDef=False):
        if isinstance(json, unicode):
            json = str(json)
        if isinstance(json, str) and not isVarDef:
            return {
                "path": MacroExpander().expand_macros(json, variables), 
                "variables": dict(variables)
                }
        if isinstance(json, dict) and sorted(json.keys()) == ["path", "variables"]:
            user_variables = MacroExpander().expand_macros(json["variables"], variables)
            final_variables = dict(variables)
            final_variables.update(user_variables)

            return {
                "path": MacroExpander().expand_macros(json["path"], variables),
                "variables": final_variables
                }

        return MacroExpander.expand_macros(self, json, variables, isVarDef)

def extract_postprocessed_test_definitions(extension, text, variable_definitions=None):
    if isinstance(text, unicode):
        text = str(text)

    if extension == ".yml":
        test_defs_json = yaml.load(text)
    elif extension == ".json":
        test_defs_json = simplejson.loads(text)
    else:
        raise Exception("Can't load testDefinitions from file ending in '%s'. Use json or yml." % extension)

    def expandKey(k):
        if k in ("environments", "builds", "tests", "deployments", "repos"):
            return MacroExpander().expand_macros(test_defs_json[k], variable_definitions or {})

        if k == "includes":
            return IncludesMacroExpander().expand_macros(test_defs_json[k], variable_definitions or {})

        return test_defs_json[k]

    return { k: expandKey(k) for k in test_defs_json }

def parseRepoReference(encoder, value):
    if isinstance(value, (str, unicode)):
        return RepoReference.Reference(reference=str(value))
    return algebraic_to_json.Encoder().from_json(value, RepoReference)

def parseVariableDict(encoder, value):
    def convert(k):
        if isinstance(k,str):
            return k
        if isinstance(k, bool):
            return "true" if k else "false"
        if isinstance(k, (float,int)):
            return str(k)
        assert False, "Unsupported variable value: %s" % k
    return {convert(k): convert(v) for k,v in value.iteritems()}

encoder = algebraic_to_json.Encoder()
encoder.overrides[VariableDict] = parseVariableDict
encoder.overrides[RepoReference] = parseRepoReference

def extract_tests_from_str(repoName, commitHash, extension, text, variable_definitions=None, externally_defined_repos=None):
    test_defs_json = extract_postprocessed_test_definitions(extension, text, variable_definitions)
    
    if 'looper_version' not in test_defs_json:
        raise Exception("No looper version specified. Valid versions are 2 <= version <= 3")

    version = test_defs_json['looper_version']
    del test_defs_json['looper_version']

    if version not in VALID_VERSIONS:
        raise Exception("Can't handle looper version %s" % version)

    return extract_tests(repoName, commitHash, encoder.from_json(test_defs_json, TestDefinitionScript), version, externally_defined_repos)
