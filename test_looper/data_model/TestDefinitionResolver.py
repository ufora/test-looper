import os
import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.core.GraphUtil as GraphUtil
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import fnmatch
import logging
import re
import test_looper.core.algebraic_to_json as algebraic_to_json
from test_looper.core.hash import sha_hash

sha_pattern = re.compile("^[a-f0-9]{40}$")
def isValidCommitRef(committish):
    return sha_pattern.match(committish)

MAX_INCLUDE_ATTEMPTS = 128

class EVERYTHING:
    pass

def pathJoin(path1, path2):
    """Given two /-separated relative filesystem paths, join them."""
    if not path1:
        return path2
    if not path2:
        return path1
    return "/".join(path1.split("/") + path2.split("/"))

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

        #(repo, hash) -> (test_set_name -> [test_name], {triggered_test_set_names})
        self.triggeredTestSetsCache = {}

    def unprocessedRepoPinsFor(self, repoName, commitHash):
        repos = self.unprocessedTestsEnvsAndReposFor_(repoName, commitHash)[2]

        return {k:v for k,v in repos.iteritems() if v.matches.Pin}

    def unprocessedTestsEnvsAndReposFor_(self, repoName, commitHash):
        if (repoName, commitHash) in self.rawDefinitionsCache:
            return self.rawDefinitionsCache[repoName, commitHash]

        textAndExtension = self.testDefinitionTextAndExtensionFor(repoName, commitHash)

        if textAndExtension is None or textAndExtension[1] is None:
            self.rawDefinitionsCache[repoName, commitHash] = ({}, {}, {}, {}, {}, [])
        else:
            self.rawDefinitionsCache[repoName, commitHash] = \
                TestDefinitionScript.extract_tests_from_str(repoName, commitHash, textAndExtension[1], textAndExtension[0])
            self.rawDefinitionsPath[repoName, commitHash] = textAndExtension[2]

        return self.rawDefinitionsCache[repoName, commitHash]

    def resolveRefWithinRepo(self, curRepoName, curCommitHash, nameOfRef, actualRef):
        """
        Allows subclasses to modify how we name repositories. 

        curRepoName - the name of the repo we're currently parsing
        nameOfRef - the name of the reference within the testDefinitions file
        actualRef - the RepoReference (not an Import) we're processing.
        """
        return actualRef

    def resolveRepoDefinitions_(self, curRepoName, curCommitHash, raw_repos):
        """Given a set of raw repo references, resolve local names and includes.

        Every resulting repo is an RepoReference.Pin, RepoReference.Reference or an RepoReference.ImportedReference
        """
        resolved_repos = {}

        #first, replace any 'HEAD' references with references to the current commit.
        repos = {}
        for r in raw_repos:
            if raw_repos[r].matches.Import:
                repos[r] = raw_repos[r]
            else:
                if raw_repos[r].reference == "HEAD" and curRepoName != "__root":
                    repos[r] = raw_repos[r]._withReplacement(reference=curRepoName + "/" + curCommitHash)
                else:
                    repos[r] = raw_repos[r]

        if any([r.commitHash() == "HEAD" for r in repos.values()]) and curRepoName != "__root":
            return {r: v for r,v in repos.iteritems() if v.matches.Pin or v.matches.Reference}

        def resolveRepoRef(refName, ref, pathSoFar):
            if refName in pathSoFar:
                raise TestResolutionException("Circular repo-refs: %s" % pathSoFar)

            if not ref.matches.Import:
                return self.resolveRefWithinRepo(curRepoName, curCommitHash, refName, ref)
            
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
                path=pathJoin(subref.path, ref.path),
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
            return repoRef.reponame(), repoRef.commitHash(), pathJoin(repoRef.path, "/".join(items[1:]))

    def _computeAllDiffs(self, repoName, commitHash):
        """Compute a set of all 'changed paths' for a given commit.

        In the current commit, everything is HEAD/path. In referenced repos, when
        referenced repos changed, it's repo_name/path.
        """
        repo = self.git_repo_lookup(repoName)

        parents = repo.gitCommitData(commitHash)[1]
        
        if not parents:
            #this is wrong - we should be listing _all_ the files
            return EVERYTHING

        return ["HEAD/" + x for x in repo.filesChangedBetweenCommits(commitHash, parents[0])]

    def _triggerApplies(self, allDiffs, trigger):
        for changed in allDiffs:
            for p in trigger.paths:
                if fnmatch.fnmatchcase(changed, p):
                    return True
        return False

    def testSetsFor(self, repoName, commitHash):
        """Compute the actual list of test_sets and the set test_sets triggered by this commit"""
        if (repoName, commitHash) in self.triggeredTestSetsCache:
            return self.triggeredTestSetsCache[repoName, commitHash]

        triggeredTriggerNames = set()

        repos,_,_,_,test_sets_with_globs, triggers = self.postIncludeDefinitions_(repoName, commitHash)

        if "all" not in test_sets_with_globs:
            test_sets_with_globs["all"] = "*"

        if not triggers:
            triggers = [
                TestDefinitionScript.PrioritizationTrigger.Trigger(name='', paths=["*"], test_sets=["all"])
                ]

        tests = self.testDefinitionsFor(repoName, commitHash)

        def testsMatching(globList):
            res = set()
            for t in tests:
                for g in globList:
                    if fnmatch.fnmatchcase(t,g):
                        res.add(t)
                        break

            return sorted(res)

        testSets = {k: testsMatching(v) for k,v in test_sets_with_globs.iteritems()}
        triggeredTestSets = set()

        allDiffs = self._computeAllDiffs(repoName, commitHash)

        if allDiffs is EVERYTHING:
            logging.info("Commit %s is considered to have EVERYTHING as its diffs.", commitHash)
        else:
            logging.info("Commit %s has diffs:\n%s", commitHash, "\n".join(["    " + x for x in allDiffs]))

        if allDiffs is EVERYTHING:
            triggeredTestSets.add("all")
        else:
            for t in triggers:
                logging.info("On commit %s, trigger %s applies: %s with diffs %s", commitHash, t, self._triggerApplies(allDiffs, t), allDiffs)
                if self._triggerApplies(allDiffs, t):
                    if t.name:
                        triggeredTriggerNames.add(t.name)

                    for glob in t.test_sets:
                        for s in testSets:
                            if fnmatch.fnmatchcase(s, glob):
                                triggeredTestSets.add(s)

        def expanded(testList):
            #get the closure of all internal builds
            testList = set(testList)
            while True:
                changed = False

                for t in testList:
                    for d in tests[t].dependencies.values():
                        if d.matches.InternalBuild:
                            name = d.name
                            if name not in testList:
                                changed = True
                                testList.add(name)

                if not changed:
                    return sorted(testList)

        fullTestSets = {setname: expanded(testSets[setname]) for setname in testSets}

        self.triggeredTestSetsCache[repoName, commitHash] = (fullTestSets, testSets, triggeredTestSets, triggeredTriggerNames)

        return self.triggeredTestSetsCache[repoName, commitHash]

    def postIncludeDefinitions_(self, repoName, commitHash):
        if (repoName, commitHash) in self.postIncludeDefinitionsCache:
            return self.postIncludeDefinitionsCache[repoName, commitHash]

        tests, envs, repos, includes, test_sets, triggers = self.unprocessedTestsEnvsAndReposFor_(repoName, commitHash)

        repos = self.resolveRepoDefinitions_(repoName, commitHash, repos)

        if any([r.commitHash() == "HEAD" for r in repos.values()]) and repoName != "__root":
            #this reference is not a reference to a _real_ commit, so don't try to follow it. We do want the repo
            #pins to be available, however, so we don't fail parsing.
            self.postIncludeDefinitionsCache[repoName, commitHash] = ({},{},repos,{})
            return self.postIncludeDefinitionsCache[repoName, commitHash]

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
                        "Can't find path %s in %s/%s" % (
                            includePath, 
                            includeRepo,
                            includeHash
                            )
                        )

                new_tests, new_envs, new_repos, new_includes, subTestSets, subPrioritizeTriggers = TestDefinitionScript.extract_tests_from_str(
                    #pass the current reponame and commit hash, not the repo/commit of the source file
                    #because we want the environments to be constructed as if they were part of this file's text.
                    repoName,
                    commitHash, 
                    os.path.splitext(includePath)[1], 
                    contents,
                    variable_definitions=variable_defs,
                    externally_defined_repos=repos,
                    allowEarlierCompatibleVersions=True
                    )

                if subTestSets or subPrioritizeTriggers:
                    raise TestResolutionException("include targets can't have test_sets or triggers")

                for reponame in new_repos:
                    if reponame in repos:
                        raise TestResolutionException("Name %s can't be defined a second time in include %s/%s/%s" % (
                            reponame, includeRepo, includeHash, includePath
                            ))
                    if new_repos[reponame].matches.Pin and new_repos[reponame].auto:
                        raise TestResolutionException("Included repo %s can't be marked 'auto'" % reponame)

                repos.update(new_repos)
                repos = self.resolveRepoDefinitions_(repoName, commitHash, repos)

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

        self.postIncludeDefinitionsCache[repoName, commitHash] = (tests, envs, repos, includes, test_sets, triggers)

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
                    real_hash = self.mostRecentHashForSubpath(
                        testDep.repo,
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
                    real_hash = self.mostRecentHashForSubpath(
                        ref.reponame(),
                        ref.commitHash(),
                        pathJoin(ref.path, testDep.path)
                        )
                else:
                    real_hash = ref.commitHash()

                return TestDefinition.TestDependency.Source(
                    repo=ref.reponame(), 
                    commitHash=real_hash,
                    path=pathJoin(ref.path, testDep.path)
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
                        "Can't find dockerfile %s in %s/%s" % (
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
                    test_stages=(),
                    test_configuration="",
                    test_timeout=0,
                    test_min_cores=0,
                    test_max_cores=0,
                    test_min_ram_gb=0,
                    test_min_disk_gb=0,
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
        if not isValidCommitRef(commitHash):
            return None, None

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
        if not isValidCommitRef(commitHash):
            return None

        git_repo = self.git_repo_lookup(repoName)
        
        return git_repo.getFileContents(commitHash, path)

    def assertTestsNoncircular_(self, tests):
        def children(t):
            try:
                return (
                    [self.resolveTestNameToTestAndArtifact(dep.name, tests)[0] 
                        for dep in tests[t].dependencies.values() if dep.matches.InternalBuild]
                        if t in tests else []
                    )
            except UserWarning as e:
                raise UserWarning("While processing test %s:\n%s" % (t, e))

        cycle = GraphUtil.graphFindCycleMultipleRoots(
            tests,
            children
            )

        if cycle:
            raise TestResolutionException("Circular test dependency found: %s" % (" -> ".join(cycle)))

    def resolveTestNameToTestAndArtifact(self, testName, testSet, ignoreArtifactResolution=False):
        """Given a name like 'test/artifact', search for it amongst testSet

        a shorter name will always match first. E.g. if we have tests "A" and
        "A/artifact", then "A/artifact" will always be masked by 'A'
        
        returns the referenced test and the artifact name as a pair
        """

        parts = testName.split("/")
        for i in xrange(len(parts)+1):
            if "/".join(parts[:i]) in testSet:
                name, artifact = "/".join(parts[:i]), "/".join(parts[i:])

                if not ignoreArtifactResolution:
                    validArtifacts = set()
                    for stage in testSet[name].stages:
                        for a in stage.artifacts:
                            validArtifacts.add(a.name)

                    if artifact not in validArtifacts:
                        raise UserWarning("Can't resolve artifact '%s' in test %s. Valid are %s." % (
                            artifact, name, sorted(validArtifacts)
                            ))

                return name, artifact

        raise UserWarning("Can't resolve %s to a valid name amongst:\n%s" % (
            testName,
            "\n".join(["  " + x for x in sorted(testSet)])
            ))

    def mostRecentHashForSubpath(self, repo, commitHash, path):
        res = self.git_repo_lookup(repo).mostRecentHashForSubpath(commitHash, path)

        if not res:
            raise UserWarning("Can't find a recent hash in %s/%s path %s" % (repo, commitHash, path))

        return res

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
                    real_hash = self.mostRecentHashForSubpath(testDep.repo, testDep.commitHash, testDep.path)
                else:
                    real_hash = testDep.commitHash

                return TestDefinition.TestDependency.Source(
                    repo=testDep.repo, 
                    commitHash=real_hash,
                    path=testDep.path
                    )

            if testDep.matches.InternalBuild:
                name,artifact = self.resolveTestNameToTestAndArtifact(testDep.name, tests)

                return TestDefinition.TestDependency.Build(
                    buildHash=resolveTest(name).hash,
                    name=name,
                    artifact=artifact
                    )

            if testDep.matches.ExternalBuild:
                assert not (testDep.repo == repoName and testDep.commitHash == commitHash), testDep

                externalTests = self.testDefinitionsFor(testDep.repo, testDep.commitHash)

                name, artifact = self.resolveTestNameToTestAndArtifact(testDep.name, externalTests)

                return TestDefinition.TestDependency.Build(
                    buildHash=externalTests[name].hash,
                    name=name,
                    artifact=artifact
                    )

            if testDep.matches.UnresolvedExternalBuild or testDep.matches.UnresolvedSource:
                if testDep.repo_name not in resolved_repos:
                    raise TestResolutionException("Test depends on unknown reponame: %s" % testDep.repo_name)
                
                ref = resolved_repos[testDep.repo_name]
                
                if testDep.matches.UnresolvedExternalBuild:
                    if ref.path:
                        raise TestResolutionException(
                            ("Can't depend on build %s from a repo-def that's" +
                                " a slice of a repo (%s has path %s)")
                             % (testDep.name, testDep.repo_name, ref.path)
                            )

                    return resolveTestDep(
                        TestDefinition.TestDependency.ExternalBuild(
                            repo=ref.reponame(), 
                            commitHash=ref.commitHash(), 
                            name=testDep.name
                            )
                        )
                else:
                    if testDep.path:
                        real_hash = self.mostRecentHashForSubpath(
                            ref.reponame(), 
                            ref.commitHash(),
                            pathJoin(ref.path, testDep.path)
                            )
                    else:
                        real_hash = ref.commitHash()

                    return TestDefinition.TestDependency.Source(
                        repo=ref.reponame(), 
                        commitHash=real_hash,
                        path=pathJoin(ref.path, testDep.path)
                        )

            return testDep

        def resolveTest(testName):
            if testName not in resolved_tests:
                try:
                    if testName not in tests:
                        raise TestResolutionException(
                            "Can't find build %s in\n%s" % (testName, "\n".join(["\t" + x for x in sorted(tests)]))
                            )
                    testDef = tests[testName]

                    self.assertArtifactSetValid(testDef)

                    for stage in testDef.stages:
                        if stage.matches.TestStage and not testDef.matches.Test:
                            raise TestResolutionException("Can't have a 'TestStage' in a build. (%s)" % testName)

                    resolved_tests[testName] = testDef._withReplacement(
                        dependencies={k:resolveTestDep(v) for k,v in testDef.dependencies.iteritems()},
                        stages=self.sortTestStages(testDef.stages)
                        )

                    resolved_tests[testName]._withReplacement(hash=sha_hash(resolved_tests[testName]).hexdigest)
                except UserWarning as e:
                    raise UserWarning("While processing test %s:\n%s" % (testName, e))


            return resolved_tests[testName]

        for t in tests:
            resolveTest(t)

        self.testDefinitionCache[repoName, commitHash] = resolved_tests

        return resolved_tests

    def sortTestStages(self, stages):
        """A stable sort of 'stages' by 'order'"""
        orders = {}
        for stage in stages:
            if stage.order not in orders:
                orders[stage.order] = []
            orders[stage.order].append(stage)

        result = []
        for o in sorted(orders):
            result.extend(orders[o])
        return result


    def assertArtifactSetValid(self, testDef):
        validArtifacts = set()
        for stage in testDef.stages:
            for a in stage.artifacts:
                if a.name in validArtifacts:
                    if not a.name:
                        raise UserWarning(("Test %s defined the unnamed artifact twice. "
                            "check whether a naked 'command' (outside of a stage) exists in the build definition "
                            "since that implies a global artifact of the entire TEST_BUILD_OUTPUT_DIR"
                            ) % testDef.name)
                    raise UserWarning("Test %s defined artifact %s twice" % (testDef.name, repr(a.name)))
                validArtifacts.add(a.name)
        if "" in validArtifacts and len(validArtifacts) > 1:
            raise UserWarning("Test %s can only define the unnamed artifact if it defines no others" % (testDef.name))




    def testEnvironmentAndRepoDefinitionsFor(self, repoName, commitHash):
        return (
            self.testDefinitionsFor(repoName, commitHash), 
            self.environmentsFor(repoName, commitHash),
            self.repoReferencesFor(repoName, commitHash)
            )
