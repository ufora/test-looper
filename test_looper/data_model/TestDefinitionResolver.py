import os
import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.core.GraphUtil as GraphUtil
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript

class MissingDependencyException(Exception):
    def __init__(self, reponame, commitHash):
        self.reponame = reponame
        self.commitHash = commitHash

    def __str__(self):
        if self.commitHash is None:
            return "MissingDependencyException(repo=%s)" % self.reponame
        return "MissingDependencyException(repo=%s, commit=%s)" % (self.reponame, self.commitHash)

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

            assert not dep.matches.UnresolvedReference, dep

            underlying_env = self.environmentDefinitionFor(dep.repo, dep.commitHash, dep.name)

            assert underlying_env is not None, "Can't find environment for %s/%s/%s" % (dep.repo, dep.commitHash, dep.name)

            dependencies[dep] = underlying_env

            if underlying_env.matches.Import:
                for dep in underlying_env.imports:
                    import_dep(dep)

        for dep in environment.imports:
            import_dep(dep)

        merged = TestDefinition.merge_environments(environment, dependencies)

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

        bad_test_envs = [t for t in tests if not tests[t].environment.matches.Environment]

        if bad_test_envs:
            raise Exception("Tests %s resolved to environments that didn't specify an image or platform" % bad_test_envs)

        return tests, envs, repos

    def testEnvironmentAndRepoDefinitionsFor(self, repoName, commitHash):
        if (repoName, commitHash) not in self.definitionCache:
            self.definitionCache[(repoName, commitHash)] = \
                self.testEnvironmentAndRepoDefinitionsFor_(repoName, commitHash)

        return self.definitionCache[(repoName, commitHash)]

    def testDefinitionTextAndExtensionFor(self, repoName, commitHash):
        repo = self.git_repo_lookup(repoName)

        if not repo:
            raise MissingDependencyException(repoName, None)

        if not repo.commitExists(commitHash):
            raise MissingDependencyException(repoName, commitHash)

        path = repo.getTestDefinitionsPath(commitHash)

        if path is None:
            return None, None

        testText = repo.getFileContents(commitHash, path)

        return testText, os.path.splitext(path)[1]

        
    def testEnvironmentAndRepoDefinitionsFor_(self, repoName, commitHash):
        textAndExtension = self.testDefinitionTextAndExtensionFor(repoName, commitHash)

        if textAndExtension is None or textAndExtension[1] is None:
            return {}, {}, {}

        tests, envs, repos = \
            TestDefinitionScript.extract_tests_from_str(repoName, commitHash, textAndExtension[1], textAndExtension[0])

        resolved_repos = {}
        resolved_tests = {}
        resolved_envs = {}

        def resolveRepoRef(refName, ref, pathSoFar):
            if refName in pathSoFar:
                raise Exception("Circular repo-refs: %s" % pathSoFar)

            if not ref.matches.Import:
                return ref
            
            if refName in resolved_repos:
                return resolved_repos[refName]

            importSeq = getattr(ref, "import").split("/")

            if importSeq[0] not in repos:
                raise Exception("Can't resolve reference to repo def %s" % (
                    importSeq[0]
                    ))

            subref_parent_repo = repoName
            subref = resolveRepoRef(importSeq[0], repos[importSeq[0]], pathSoFar + (ref,))

            for s in importSeq[1:]:
                subref_parent_repo = subref.reponame()

                repos_for_subref = self.testEnvironmentAndRepoDefinitionsFor(subref.reponame(), subref.commitHash())[2]

                if s not in repos_for_subref:
                    raise Exception("Can't resolve reference %s because %s/%s doesn't have %s" % 
                        (importSeq, subref.reponame(), subref.commitHash(), s))

                subref = repos_for_subref[s]

                assert not subref.matches.Import

            #make sure it's not a pin - we don't want to create a pin for it!
            subref = TestDefinition.RepoReference.ImportedReference(
                reference=subref.reference,
                import_source=getattr(ref, "import"),
                orig_reference="" if subref.matches.Reference
                    else subref.orig_reference if subref.matches.ImportedReference 
                    else subref_parent_repo + "/" + subref.branch
                )

            resolved_repos[refName] = subref

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

        def children(t):
            return (
                [dep.name for dep in tests[t].dependencies.values() if dep.matches.InternalBuild]
                    if t in tests else []
                )

        cycle = GraphUtil.graphFindCycleMultipleRoots(
            tests,
            children
            )

        if cycle:
            raise Exception("Circular test dependency found: %s" % (" -> ".join(cycle)))

        for r in repos:
            resolved_repos[r] = resolveRepoRef(r, repos[r], ())

        for e in envs:
            resolved_envs[e] = resolveEnvironment(envs[e])

        for t in tests:
            resolved_tests[t] = resolveTest(tests[t])

        return resolved_tests, resolved_envs, resolved_repos



