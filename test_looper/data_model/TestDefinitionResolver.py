import os
import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.core.GraphUtil as GraphUtil
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript

class MissingDependencyException(Exception):
    def __init__(self, reponame, commitHash):
        self.reponame = reponame
        self.commitHash = commitHash

class TestDefinitionResolver:
    def __init__(self, git_repo_lookup):
        self.git_repo_lookup = git_repo_lookup
        self.definitionCache = {}

    def resolveEnvironment(self, environment):
        if environment.matches.Environment:
            return TestDefinition.apply_environment_substitutions(environment)

        dependencies = {}

        def import_dep(dep):
            """Grab a dependency and all its children and stash them in 'dependencies'"""
            if dep in dependencies:
                return

            underlying_env = self.environmentDefinitionFor(dep.repo, dep.commitHash, dep.name)

            assert underlying_env is not None, "Can't find environment for %s/%s/%s" % (dep.repo, dep.commitHash, dep.name)

            dependencies[dep] = underlying_env

            if underlying_env.matches.Import:
                for dep in underlying_env.imports:
                    import_dep(dep)

        for dep in environment.imports:
            import_dep(dep)

        merged = TestDefinition.merge_environments(environment, dependencies)

        if merged.matches.Import:
            raise Exception("Environment didn't resolve to a real environment: inheritance is %s", merged.inheritance)

        return TestDefinition.apply_environment_substitutions(merged)


    def environmentDefinitionFor(self, repoName, commitHash, envName):
        return self.testEnvironmentAndRepoDefinitionsFor(repoName, commitHash)[1].get(envName)

    def fullyResolvedTestEnvironmentAndRepoDefinitionsFor(self, repoName, commitHash):
        tests, envs, repos = self.testEnvironmentAndRepoDefinitionsFor(repoName, commitHash)

        envs = {e:self.resolveEnvironment(env) for e,env in envs.iteritems()}
        tests = {t: test._withReplacement(
                        environment=self.resolveEnvironment(test.environment)
                        )
                for t, test in tests.iteritems()}

        return tests, envs, repos

    def testEnvironmentAndRepoDefinitionsFor(self, repoName, commitHash):
        if (repoName, commitHash) not in self.definitionCache:
            self.definitionCache[(repoName, commitHash)] = \
                self.testEnvironmentAndRepoDefinitionsFor_(repoName, commitHash)

        return self.definitionCache[(repoName, commitHash)]

    def testEnvironmentAndRepoDefinitionsFor_(self, repoName, commitHash):
        repo = self.git_repo_lookup(repoName)

        if not repo:
            raise MissingDependencyException(repoName, None)

        if not repo.source_repo.commitExists(commitHash):
            raise MissingDependencyException(repoName, commitHash)

        path = repo.source_repo.getTestDefinitionsPath(commitHash)

        if path is None:
            return {}, {}, {}

        testText = repo.source_repo.getFileContents(commitHash, path)

        tests, envs, repos = \
            TestDefinitionScript.extract_tests_from_str(repoName, commitHash, os.path.splitext(path)[1], testText)

        resolved_repos = {}
        resolved_tests = {}
        resolved_envs = {}

        def resolveRepoRef(ref, pathSoFar):
            if ref in pathSoFar:
                raise Exception("Circular repo-refs: %s" % pathSoFar)

            if not ref.matches.Import:
                return ref
            
            if ref in resolved_repos:
                return resolved_repos[ref]

            importSeq = getattr(ref, "import").split(".")

            subref = resolveRepoRef(importSeq[0], pathSoFar + (ref,))

            for s in importSeq[1:]:
                repos = self.testEnvironmentAndRepoDefinitionsFor(subref.reponame(), subref.commitHash())[2]

                if s not in repos:
                    raise Exception("Can't resolve reference %s because %s/%s doesn't have %s" % 
                        (importSeq, subref.reponame(), subref.commitHash(), s))

                subref = repos[s]

                assert not subref.matches.Import

            return subref

        def resolveEnvironmentReference(env):
            if not env.matches.UnresolvedReference:
                return env 

            if env.repo_name not in resolved_repos:
                #this shouldn't happen because the test extractor should check this already
                raise Exception("Environment depends on unknown reponame: %s" % env.repo_name)

            ref = resolved_repos[env.repo_name]

            return TestDefinition.EnvironmentReference(
                repo=ref.reponame(), 
                commitHash=ref.commitHash(), 
                name=env.name
                )

        def resolveTestDep(testDep):
            if testDep.matches.UnresolvedExternalBuild or testDep.matches.UnresolvedSource:
                if testDep.repo_name not in resolved_repos:
                    raise Exception("Test depends on unknown reponame: %s" % testDep.repo_name)
                
                ref = resolved_repos[testDep.repo_name]
                
                if testDep.matches.UnresolvedExternalBuild:
                    return TestDefinition.TestDependency.ExternalBuild(
                        repo=ref.reponame(), 
                        commitHash=ref.commitHash(), 
                        name=testDep.name
                        )
                else:
                    return TestDefinition.TestDependency.Source(
                        repo=ref.reponame(), 
                        commitHash=ref.commitHash()
                        )

            return testDep

        def resolveEnvironment(env):
            if env.matches.Environment:
                return TestDefinition.TestEnvironment.Environment(
                    environment_name=env.environment_name,
                    inheritance=env.inheritance,
                    platform=env.platform,
                    image=env.image,
                    variables=env.variables,
                    dependencies={depname: resolveTestDep(dep) for depname, dep in env.dependencies.iteritems()}
                    )
            else:
                return TestDefinition.TestEnvironment.Import(
                    environment_name=env.environment_name,
                    inheritance=env.inheritance,
                    imports=[resolveEnvironmentReference(e) for e in env.imports],
                    setup_script_contents=env.setup_script_contents,
                    variables=env.variables,
                    dependencies={depname: resolveTestDep(dep) for depname, dep in env.dependencies.iteritems()}
                    )

        def resolveTest(testDef):
            #we could also check for circularity here
            return testDef._withReplacement(
                dependencies={k:resolveTestDep(v) for k,v in testDef.dependencies.iteritems()},
                environment=resolveEnvironment(testDef.environment)
                )

        cycle = GraphUtil.graphFindCycleMultipleRoots(
            tests, 
            lambda t: [dep.name for dep in tests[t].dependencies.values() if dep.matches.InternalBuild]
            )

        if cycle:
            raise Exception("Circular test dependency found: %s" % (cycle,))

        for r in repos:
            resolved_repos[r] = resolveRepoRef(repos[r], (r,))

        for e in envs:
            resolved_envs[e] = resolveEnvironment(envs[e])

        for t in tests:
            resolved_tests[t] = resolveTest(tests[t])

        return resolved_tests, resolved_envs, resolved_repos



