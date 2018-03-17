import os
import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.core.GraphUtil as GraphUtil
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript

from test_looper.core.hash import sha_hash

MAX_INCLUDE_ATTEMPTS = 128

class TestResolutionException(Exception):
    def __init__(self, msg):
        Exception.__init__(self, msg)

class MissingDependencyException(TestResolutionException):
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
        
        #(repo, hash) -> (tests, envs, repos, includes) in unprocessed form
        self.rawDefinitionsCache = {}

        #(repo, hash) -> path-in-repo-totests
        self.rawDefinitionsPath = {}

        #(repo, hash) -> (tests, envs, repos, includes) after processing repos and merging in imports
        self.postIncludeDefinitionsCache = {}

        #(repo, hash) -> env_name -> environment
        self.environmentCache = {}

        #(repo, hash) -> test_name -> test_definition
        self.testDefinitionCache = {}

    def unprocessedTestsEnvsAndReposFor_(self, repoName, commitHash):
        if (repoName, commitHash) in self.rawDefinitionsCache:
            return self.rawDefinitionsCache[repoName, commitHash]

        textAndExtension = self.testDefinitionTextAndExtensionFor(repoName, commitHash)

        if textAndExtension is None or textAndExtension[1] is None:
            self.rawDefinitionsCache[repoName, commitHash] = ({}, {}, {}, {})
        else:
            self.rawDefinitionsCache[repoName, commitHash] = \
                TestDefinitionScript.extract_tests_from_str(repoName, commitHash, textAndExtension[1], textAndExtension[0])
            self.rawDefinitionsPath[repoName, commitHash] = textAndExtension[2]

        return self.rawDefinitionsCache[repoName, commitHash]

    def resolveRepoDefinitions_(self, curRepoName, repos):
        """Given a set of raw repo references, resolve local names and includes.

        Every resulting repo is an RepoReference.Pin, RepoReference.Reference or an RepoReference.ImportedReference
        """
        resolved_repos = {}

        def resolveRepoRef(refName, ref, pathSoFar):
            if refName in pathSoFar:
                raise TestResolutionException("Circular repo-refs: %s" % pathSoFar)

            if not ref.matches.Import:
                return ref
            
            if refName in resolved_repos:
                return resolved_repos[refName]

            importSeq = getattr(ref, "import").split("/")

            if importSeq[0] not in repos:
                raise TestResolutionException("Can't resolve reference to repo def %s" % (
                    importSeq[0]
                    ))

            subref_parent_repo = curRepoName
            subref = resolveRepoRef(importSeq[0], repos[importSeq[0]], pathSoFar + (ref,))

            for s in importSeq[1:]:
                subref_parent_repo = subref.reponame()

                repos_for_subref = self.repoReferencesFor(subref.reponame(), subref.commitHash())

                if s not in repos_for_subref:
                    raise TestResolutionException("Can't resolve reference %s because %s/%s doesn't have %s" % 
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

        return resolved_repos

    def resolveIncludeString_(self, repos, repoName, commitHash, path):
        def resolvePath(path):
            """Resolve a path as if it were a linux path (using /). Can't use os.path.join
            because that's not platform-independent"""
            items = path.split("/")
            i = 0
            while i < len(items):
                if items[i] == ".":
                    items.pop(i)
                elif items[i] == ".." and i > 0:
                    items.pop(i-1)
                    items.pop(i-1)
                    i -= 1
                else:
                    i += 1
            return "/".join(items)

        items = path.split("/")
        if not (items[0] == "" or items[0] in repos or items[0] in (".","..")):
            raise TestResolutionException("Invalid include %s: should start with a repo, a '/' (for root of current repo), '.', or '..'" % i.path)

        if items[0] == "":
            return repoName, commitHash, "/".join(items[1:])

        if items[0] in (".", ".."):
            return repoName, commitHash, resolvePath(self.rawDefinitionsPath[repoName,commitHash] + "/../" + path)

        if items[0] in repos:
            repoRef = repos[items[0]]
            return repoRef.reponame(), repoRef.commitHash(), "/".join(items[1:])


    def postIncludeDefinitions_(self, repoName, commitHash):
        if (repoName, commitHash) in self.postIncludeDefinitionsCache:
            return self.postIncludeDefinitionsCache[repoName, commitHash]

        tests, envs, repos, includes = self.unprocessedTestsEnvsAndReposFor_(repoName, commitHash)

        repos = self.resolveRepoDefinitions_(repoName, repos)

        everIncluded = set()

        attempts = 0

        includes = [(repoName, commitHash, i) for i in includes]

        while includes:
            includeSourceRepo, includeSourceHash, i = includes[0]

            includes = includes[1:]

            variable_defs = dict(i.variables)
            variable_defs_as_tuple = tuple(variable_defs.items())

            includeRepo, includeHash, includePath = self.resolveIncludeString_(repos, includeSourceRepo, includeSourceHash, i.path)

            include_key = (includeRepo, includeHash, includePath, variable_defs_as_tuple)

            if include_key not in everIncluded:
                attempts += 1

                if attempts > MAX_INCLUDE_ATTEMPTS:
                    raise TestResolutionException("Exceeded the maximum number of file includes: %s" % MAX_INCLUDE_ATTEMPTS)

                everIncluded.add(include_key)

                contents = self.getRepoContentsAtPath(includeRepo, includeHash, includePath)

                if contents is None:
                    raise TestResolutionException(
                        "Can't find path %s in in %s/%s" % (
                            includePath, 
                            includeRepo,
                            includeHash
                            )
                        )

                new_tests, new_envs, new_repos, new_includes = TestDefinitionScript.extract_tests_from_str(
                    includeRepo, 
                    includeHash, 
                    os.path.splitext(includePath)[1], 
                    contents,
                    variable_definitions=variable_defs,
                    externally_defined_repos=repos
                    )

                for reponame in new_repos:
                    if reponame in repos:
                        raise TestResolutionException("Name %s can't be defined a second time in include %s/%s/%s" % (
                            reponame, includeRepo, includeHash, includePath
                            ))
                repos.update(new_repos)
                repos = self.resolveRepoDefinitions_(repoName, repos)

                for env in new_envs:
                    if env in envs or env in repos:
                        raise TestResolutionException("Name %s can't be defined a second time in include %s/%s/%s" % (
                            env, includeRepo, includeHash, includePath
                            ))
                envs.update(new_envs)

                for test in new_tests:
                    if test in tests or test in envs or test in repos:
                        raise TestResolutionException("Name %s can't be defined a second time in include %s/%s/%s" % (
                            test, includeRepo, includeHash, includePath
                            ))
                tests.update(new_tests)

                for i in new_includes:
                    includes.append((includeSourceRepo, includeSourceHash,i))

        self.postIncludeDefinitionsCache[repoName, commitHash] = (tests, envs, repos, includes)

        return self.postIncludeDefinitionsCache[repoName, commitHash]

    def repoReferencesFor(self, repoName, commitHash):
        return self.postIncludeDefinitions_(repoName, commitHash)[2]

    def assertEnvironmentsNoncircular_(self, environments, repoName, commitHash):
        def children(e):
            if e.repo == repoName and e.commitHash==commitHash:
                return e.includes
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
            raise TestResolutionException("Circular environment dependency found: %s" % (" -> ".join(cycle)))
    

    def resolveEnvironmentPreMerge_(self, environment, resolved_repos):
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
                    raise TestResolutionException("Environment depends on unknown reponame: %s" % testDep.repo_name)
                
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
                raise TestResolutionException("Environment depends on unknown reponame: %s" % env.repo_name)

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
                    raise TestResolutionException(
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

    def actualEnvironmentNameForTest_(self, testDef):
        if not testDef.environment_mixins:
            return testDef.environment_name
        else:
            return "+".join([testDef.environment_name] + list(testDef.environment_mixins))

    def environmentsFor(self, repoName, commitHash):
        if (repoName, commitHash) in self.environmentCache:
            return self.environmentCache[repoName, commitHash]

        resolved_repos = self.repoReferencesFor(repoName, commitHash)

        tests, environments = self.postIncludeDefinitions_(repoName, commitHash)[:2]

        synthetic_names = set()

        #we make fake environments for each test that uses mixins
        for testDef in tests.values():
            if testDef.environment_mixins:
                synthetic_name = self.actualEnvironmentNameForTest_(testDef)
                fakeEnvironment = TestDefinition.TestEnvironment.Import(
                    environment_name=testDef.environment_name,
                    inheritance="",
                    imports= [
                        TestDefinition.EnvironmentReference.Reference(repo=repoName, commitHash=commitHash,name=ref)
                            for ref in [testDef.environment_name] + list(testDef.environment_mixins)
                        ],
                    setup_script_contents="",
                    variables={},
                    dependencies={},
                    test_preCommand="",
                    test_preCleanupCommand="",
                    test_timeout=0,
                    test_min_cores=0,
                    test_max_cores=0,
                    test_min_ram_gb=0,
                    test_max_retries=0,
                    test_retry_wait_seconds=0
                    )
                environments[synthetic_name] = fakeEnvironment


        #resolve names for repos
        environments = {e: self.resolveEnvironmentPreMerge_(environments[e], resolved_repos) 
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
                    raise TestResolutionException("Can't find environment %s for %s/%s. Available: %s" % (
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

        return testText, os.path.splitext(path)[1], path

    def getRepoContentsAtPath(self, repoName, commitHash, path):
        git_repo = self.git_repo_lookup(repoName)
        
        return git_repo.getFileContents(commitHash, path)

    def assertTestsNoncircular_(self, tests):
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
            raise TestResolutionException("Circular test dependency found: %s" % (" -> ".join(cycle)))

    def testDefinitionsFor(self, repoName, commitHash):
        if (repoName, commitHash) in self.testDefinitionCache:
            return self.testDefinitionCache[repoName, commitHash]

        tests = self.postIncludeDefinitions_(repoName, commitHash)[0]

        resolved_repos = self.repoReferencesFor(repoName, commitHash)
        resolved_envs = self.environmentsFor(repoName, commitHash)

        def resolveTestEnvironmentAndApplyVars(testDef):
            name = self.actualEnvironmentNameForTest_(testDef)

            if name not in resolved_envs:
                raise TestResolutionException("Can't find environment %s (referenced by %s) in\n%s" % (
                    testDef.environment_name,
                    testDef.name,
                    "\n".join(["\t" + x for x in sorted(resolved_envs)])
                    ))
            env = resolved_envs[name]

            testDef = testDef._withReplacement(environment=env)
            testDef = TestDefinition.apply_environment_to_test(testDef, env, {})

            return testDef

        tests = {t:resolveTestEnvironmentAndApplyVars(tests[t]) for t in tests}

        self.assertTestsNoncircular_(tests)

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
                    raise TestResolutionException(
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
                    raise TestResolutionException("Test depends on unknown reponame: %s" % testDep.repo_name)
                
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
                    raise TestResolutionException(
                        "Can't find build %s in\n%s" % (testName, "\n".join(["\t" + x for x in sorted(tests)]))
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
