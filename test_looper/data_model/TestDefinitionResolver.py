import os
import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.core.GraphUtil as GraphUtil
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript

from test_looper.core.hash import sha_hash

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
        
        #(repo,hash) -> env -> envDef
        self.environmentsCache = {}

        #(repo, hash) -> (repos, envs, tests) in unprocessed form
        self.rawDefinitionsCache = {}

        #(repo, hash) -> repo_def -> repo_reference
        self.repoReferenceCache = {}

        #(repo, hash) -> env_name -> environment
        self.environmentCache = {}

        self.testDefinitionCache = {}

    def unprocessedTestsEnvsAndReposFor_(self, repoName, commitHash):
        if (repoName, commitHash) in self.rawDefinitionsCache:
            return self.rawDefinitionsCache[repoName, commitHash]

        textAndExtension = self.testDefinitionTextAndExtensionFor(repoName, commitHash)

        if textAndExtension is None or textAndExtension[1] is None:
            self.rawDefinitionsCache[repoName, commitHash] = ({}, {}, {})
        else:
            self.rawDefinitionsCache[repoName, commitHash] = \
                TestDefinitionScript.extract_tests_from_str(repoName, commitHash, textAndExtension[1], textAndExtension[0])

        return self.rawDefinitionsCache[repoName, commitHash]

    def repoReferencesFor(self, repoName, commitHash):
        if (repoName, commitHash) in self.repoReferenceCache:
            return self.repoReferenceCache[repoName, commitHash]

        repos = self.unprocessedTestsEnvsAndReposFor_(repoName, commitHash)[2]

        resolved_repos = {}

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

                repos_for_subref = self.repoReferencesFor(subref.reponame(), subref.commitHash())

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

        for r in repos:
            resolved_repos[r] = resolveRepoRef(r, repos[r], ())

        self.repoReferenceCache[repoName, commitHash] = resolved_repos

        return resolved_repos

    def assertEnvironmentsNoncircular(self, environments, repoName, commitHash):
        def children(e):
            if e.repo == repoName and e.commitHash==commitHash:
                return e.imports
            return []

        cycle = GraphUtil.graphFindCycleMultipleRoots(
            [TestDefinition.EnvironmentReference(
                repo=repoName,
                commitHash=commitHash,
                name=e
                )
            for e in environments]
            )

        if cycle:
            raise Exception("Circular environment dependency found: %s" % (" -> ".join(cycle)))
    

    def resolveEnvironmentPreMerge(self, environment, resolved_repos):
        """Apply logic to dependencies, images, local imports
        """
        def resolveTestDep(testDep):
            if testDep.matches.Source:
                if testDep.path:
                    real_hash = self.git_repo_lookup(testDep.repo).mostRecentHashForSubpath(
                        testDep.commitHash,
                        testDep.path
                        )
                else:
                    real_hash = testDep.commitHash

                return TestDefinition.TestDependency.Source(
                    repo=testDep.repo, 
                    commitHash=real_hash,
                    path=testDep.path
                    )

            if testDep.matches.UnresolvedSource:
                if testDep.repo_name not in resolved_repos:
                    raise Exception("Environment depends on unknown reponame: %s" % testDep.repo_name)
                
                ref = resolved_repos[testDep.repo_name]
                
                if testDep.path:
                    real_hash = self.git_repo_lookup(ref.reponame()).mostRecentHashForSubpath(
                        testDep.path
                        )
                else:
                    real_hash = ref.commitHash()

                return TestDefinition.TestDependency.Source(
                    repo=ref.reponame(), 
                    commitHash=real_hash,
                    path=testDep.path
                    )

            return testDep

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

        def resolveImage(image):
            if image.matches.Dockerfile:
                repo = self.git_repo_lookup(image.repo)
                if not repo:
                    raise MissingDependencyException(image.repo, None)

                if not repo.commitExists(image.commitHash):
                    raise MissingDependencyException(image.repo, image.commitHash)

                contents = repo.getFileContents(image.commitHash, image.dockerfile)

                if contents is None:
                    raise Exception(
                        "Can't find dockerfile %s in in %s/%s" % (
                            image.dockerfile, 
                            image.repo, 
                            image.commitHash
                            )
                        )

                return TestDefinition.Image.DockerfileInline(contents)
            return image


        environment = environment._withReplacement(dependencies=
            {depname: resolveTestDep(dep) for depname, dep in environment.dependencies.iteritems()}
            )
        
        if environment.matches.Environment:
            environment = environment._withReplacement(image=resolveImage(environment.image))
        else:
            environment = \
                environment._withReplacement(imports=[resolveEnvironmentReference(i) for i in environment.imports])

        return environment

    def environmentsFor(self, repoName, commitHash):
        if (repoName, commitHash) in self.environmentCache:
            return self.environmentCache[repoName, commitHash]

        resolved_repos = self.repoReferencesFor(repoName, commitHash)

        environments = self.unprocessedTestsEnvsAndReposFor_(repoName, commitHash)[1]

        #resolve names for repos and whatnot
        environments = {e: self.resolveEnvironmentPreMerge(environments[e], resolved_repos) 
            for e in environments}

        def resolveEnvironment(environment):
            dependencies = {}

            if environment.matches.Environment:
                return TestDefinition.apply_environment_substitutions(environment)

            def import_dep(dep):
                """Grab a dependency and all its children and stash them in 'dependencies'"""
                if dep in dependencies:
                    return

                assert not dep.matches.UnresolvedReference, dep

                if dep.repo == repoName and dep.commitHash == commitHash:
                    env_set = environments
                else:
                    env_set = self.environmentsFor(dep.repo, dep.commitHash)

                underlying_env = env_set.get(dep.name, None)
                if not underlying_env:
                    raise Exception("Can't find environment %s for %s/%s. Available: %s" % (
                        dep.name,
                        dep.repo,
                        dep.commitHash,
                        ",".join(env_set)
                        ))

                dependencies[dep] = underlying_env

                if underlying_env.matches.Import:
                    for dep in underlying_env.imports:
                        import_dep(dep)

            for dep in environment.imports:
                import_dep(dep)

            merged = TestDefinition.merge_environments(environment, dependencies)

            return TestDefinition.apply_environment_substitutions(merged)

        resolved_envs = {}

        for e in environments:
            resolved_envs[e] = resolveEnvironment(environments[e])

        self.environmentCache[repoName, commitHash] = resolved_envs

        return resolved_envs

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

    def assertTestsNoncircular(self, tests):
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

    def testDefinitionsFor(self, repoName, commitHash):
        if (repoName, commitHash) in self.testDefinitionCache:
            return self.testDefinitionCache[repoName, commitHash]

        tests = self.unprocessedTestsEnvsAndReposFor_(repoName, commitHash)[0]

        resolved_repos = self.repoReferencesFor(repoName, commitHash)
        resolved_envs = self.environmentsFor(repoName, commitHash)

        def resolveTestEnvironmentAndApplyVars(testDef):
            env = resolved_envs[testDef.environment_name]

            testDef = testDef._withReplacement(environment=env)
            testDef = TestDefinition.apply_test_substitutions(testDef, env, {})

            return testDef

        tests = {t:resolveTestEnvironmentAndApplyVars(tests[t]) for t in tests}

        self.assertTestsNoncircular(tests)

        resolved_tests = {}
        
        def resolveTestDep(testDep):
            if testDep.matches.Source:
                if testDep.path:
                    real_hash = self.git_repo_lookup(testDep.repo).mostRecentHashForSubpath(
                        testDep.commitHash,
                        testDep.path
                        )
                else:
                    real_hash = testDep.commitHash

                return TestDefinition.TestDependency.Source(
                    repo=testDep.repo, 
                    commitHash=real_hash,
                    path=testDep.path
                    )

            if testDep.matches.InternalBuild:
                return TestDefinition.TestDependency.Build(
                    repo=repoName,
                    buildHash=resolveTest(testDep.name).hash,
                    name=testDep.name
                    )

            if testDep.matches.ExternalBuild:
                assert not (testDep.repo == repoName and testDep.commitHash == commitHash)

                tests = self.testDefinitionsFor(testDep.repo, testDep.commitHash)

                if testDep.name not in tests:
                    raise Exception(
                        "Build %s doesn't exist in %s/%s. found %s" % (
                            testDep.name,
                            testDep.repo,
                            testDep.commitHash,
                            ",".join(tests) if tests else "no tests"
                            )
                        )

                return TestDefinition.TestDependency.Build(
                    repo=testDep.repo,
                    buildHash=tests[testDep.name].hash,
                    name=testDep.name
                    )

            if testDep.matches.UnresolvedExternalBuild or testDep.matches.UnresolvedSource:
                if testDep.repo_name not in resolved_repos:
                    raise Exception("Test depends on unknown reponame: %s" % testDep.repo_name)
                
                ref = resolved_repos[testDep.repo_name]
                
                if testDep.matches.UnresolvedExternalBuild:
                    return resolveTestDep(
                        TestDefinition.TestDependency.ExternalBuild(
                            repo=ref.reponame(), 
                            commitHash=ref.commitHash(), 
                            name=testDep.name
                            )
                        )
                else:
                    if testDep.path:
                        real_hash = self.git_repo_lookup(ref.reponame()).mostRecentHashForSubpath(
                            testDep.path
                            )
                    else:
                        real_hash = ref.commitHash()

                    return TestDefinition.TestDependency.Source(
                        repo=ref.reponame(), 
                        commitHash=real_hash,
                        path=testDep.path
                        )

            return testDep

        def resolveTest(testName):
            if testName not in resolved_tests:
                if testName not in tests:
                    raise Exception(
                        "Can't find build %s in %s" % (testName, ", ".join(tests))
                        )
                testDef = tests[testName]

                resolved_tests[testName] = testDef._withReplacement(
                    dependencies={k:resolveTestDep(v) for k,v in testDef.dependencies.iteritems()}
                    )

                resolved_tests[testName]._withReplacement(hash=sha_hash(resolved_tests[testName]).hexdigest)

            return resolved_tests[testName]

        for t in tests:
            resolveTest(t)

        self.testDefinitionCache[repoName, commitHash] = resolved_tests

        return resolved_tests

    def testEnvironmentAndRepoDefinitionsFor(self, repoName, commitHash):
        return (
            self.testDefinitionsFor(repoName, commitHash), 
            self.environmentsFor(repoName, commitHash),
            self.repoReferencesFor(repoName, commitHash)
            )
