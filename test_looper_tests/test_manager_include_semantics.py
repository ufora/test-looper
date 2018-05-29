import unittest
import os
import logging

import test_looper_tests.common as common
import test_looper_tests.TestYamlFiles as TestYamlFiles
import test_looper_tests.TestManagerTestHarness as TestManagerTestHarness
import test_looper.data_model.BranchPinning as BranchPinning
import test_looper.data_model.ImportExport as ImportExport
import test_looper.data_model.TestDefinitionResolver as TestDefinitionResolver
import textwrap

common.configureLogging()

class TestManagerIncludeSemanticsTests(unittest.TestCase):
    def test_basic_includes(self):
        repo_include_envdef = textwrap.dedent("""
            looper_version: 5
            environments:
              ${env_name}: 
                platform: linux
                image:
                  dockerfile_contents: hi
                variables:
                  ${vname}: ${vdef}
            """)

        repo = textwrap.dedent("""
            looper_version: 5
            repos:
              include_from: 
                reference: repo0/base
            includes:
              foreach:
                env_name:
                  - e1
                  - e2
                vname:
                  - v1
                vdef:
                  - v2
              repeat:
                - include_from/envdef.yml
            """)

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/base",[], "", {"envdef.yml": repo_include_envdef})
        harness.manager.source_control.addCommit("repo0/c0", [], repo)

        resolver = harness.resolver()
        
        self.assertTrue(sorted(resolver.environmentsFor("repo0", "c0").keys()) == ["e1","e2"])

    def test_include_includes(self):
        envdef2 = textwrap.dedent("""
            looper_version: 5
            repos:
              r: repo0/base
            """)

        envdef = textwrap.dedent("""
            looper_version: 5
            includes:
              - ./envdef2.yml
            """)

        repo = textwrap.dedent("""
            looper_version: 5
            includes:
              - ./envdef.yml
            """
            )

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], repo, {"envdef.yml": envdef, "envdef2.yml": envdef2})

        resolver = harness.resolver()
        
        self.assertEqual(sorted(resolver.repoReferencesFor("repo0", "c0").keys()), ["r"])

    def test_repos_with_paths(self):
        yaml_with_repo_paths = textwrap.dedent("""
          looper_version: 5
          repos:
            self_with_path: 
              reference: HEAD
              path: dir1/dir2
            self_without_path: 
              reference: HEAD
          environments:
            test_linux:
              platform: linux
              image:
                dockerfile: "test_looper/Dockerfile.txt"
          builds:
            way_1:
              environment: test_linux
              command: "src/build.sh $TEST_INPUTS/child"
              dependencies:
                src: self_with_path
            way_2:
              environment: test_linux
              command: "src/build.sh $TEST_INPUTS/child"
              dependencies:
                src: self_without_path/source/dir1/dir2
          """)
      
        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], yaml_with_repo_paths)

        resolver = harness.resolver()
        
        tests = resolver.testDefinitionsFor("repo0", "c0")

        self.assertTrue('way_1' in tests, tests.keys())
        self.assertTrue('way_2' in tests, tests.keys())

        self.assertEqual(
          tests['way_1'].dependencies['test_inputs/src'],
          tests['way_2'].dependencies['test_inputs/src']
          )



    def test_recursive_includes(self):
        envdef2 = textwrap.dedent("""
            looper_version: 5
            includes:
              - ./envdef.yml
            """)

        envdef = textwrap.dedent("""
            looper_version: 5
            includes:
              - ./envdef2.yml
            """)

        repo = textwrap.dedent("""
            looper_version: 5
            includes:
              - ./envdef.yml
            """
            )

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], repo, {"envdef.yml": envdef, "envdef2.yml": envdef2})

        resolver = harness.resolver()
        
        self.assertEqual(sorted(resolver.repoReferencesFor("repo0", "c0").keys()), [])

    def test_env_inheritance_in_included_files(self):
        lowest = textwrap.dedent("""
            looper_version: 5
            environments:
              root_env:
                platform: linux
                image:
                  dockerfile_contents: hi
              derived:
                base: [root_env]
            """)

        repo = textwrap.dedent("""
            looper_version: 5
            repos:
              r: repo0/c0
            includes:
              - r/lowest.yml
            environments:
              really_derived:
                base: [ derived ]
            """
            )

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], "looper_version: 5", {"lowest.yml": lowest})
        harness.manager.source_control.addCommit("repo0/c1", [], repo)

        resolver = harness.resolver()
        
        self.assertEqual(sorted(resolver.environmentsFor("repo0", "c1").keys()), ["derived", "really_derived", "root_env"])

    def test_recursive_includes_with_variables_that_expand_forever(self):
        envdef2 = textwrap.dedent("""
            looper_version: 5
            includes:
              - path: ./envdef.yml
                variables:
                  var: v_${var}
            """)

        envdef = textwrap.dedent("""
            looper_version: 5
            includes:
              - path: ./envdef2.yml
                variables:
                  var: v_${var}
            """)

        repo = textwrap.dedent("""
            looper_version: 5
            includes:
              - path: ./envdef.yml
                variables:
                  var: v_0
            """
            )

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], repo, {"envdef.yml": envdef, "envdef2.yml": envdef2})

        resolver = harness.resolver()
        
        with self.assertRaises(TestDefinitionResolver.TestResolutionException):
            resolver.repoReferencesFor("repo0", "c0")

    def test_includes_cant_redefine_repos(self):
        envdef2 = textwrap.dedent("""
            looper_version: 5
            repos:
              r: repo0/base
            """)

        envdef = textwrap.dedent("""
            looper_version: 5
            repos:
              r: repo0/base2
            """)

        repo = textwrap.dedent("""
            looper_version: 5
            includes:
              - ./envdef.yml
              - ./envdef2.yml
            """
            )

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/base", [], None)
        harness.manager.source_control.addCommit("repo0/base2", [], None)

        harness.manager.source_control.addCommit("repo0/c0", [], repo, {"envdef.yml": envdef, "envdef2.yml": envdef2})

        resolver = harness.resolver()
        
        with self.assertRaises(TestDefinitionResolver.TestResolutionException):
            resolver.repoReferencesFor("repo0", "c0")

    def test_includes_cant_redefine_environments(self):
        envdef2 = textwrap.dedent("""
            looper_version: 5
            environments:
              e: 
                platform: linux
                image:
                  dockerfile_contents: hi
            """)

        envdef = textwrap.dedent("""
            looper_version: 5
            environments:
              e: 
                platform: linux
                image:
                  dockerfile_contents: hi
            """)

        repo = textwrap.dedent("""
            looper_version: 5
            includes:
              - ./envdef.yml
              - ./envdef2.yml
            """
            )

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/base", [], None)
        harness.manager.source_control.addCommit("repo0/base2", [], None)

        harness.manager.source_control.addCommit("repo0/c0", [], repo, {"envdef.yml": envdef, "envdef2.yml": envdef2})

        resolver = harness.resolver()
        
        with self.assertRaises(TestDefinitionResolver.TestResolutionException):
            resolver.repoReferencesFor("repo0", "c0")

    def test_includes_cant_redefine_tests(self):
        envdef2 = textwrap.dedent("""
            looper_version: 5
            environments:
              e1: 
                platform: linux
                image:
                  dockerfile_contents: hi
            tests:
              t:
                environment: e1
                command: "./script.py 1"
            """)

        envdef = textwrap.dedent("""
            looper_version: 5
            environments:
              e2: 
                platform: linux
                image:
                  dockerfile_contents: hi
            tests:
              t:
                environment: e2
                command: "./script.py 1"
            """)

        repo = textwrap.dedent("""
            looper_version: 5
            includes:
              - ./envdef.yml
              - ./envdef2.yml
            """
            )

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/base", [], None)
        harness.manager.source_control.addCommit("repo0/base2", [], None)

        harness.manager.source_control.addCommit("repo0/c0", [], repo, {"envdef.yml": envdef, "envdef2.yml": envdef2})

        resolver = harness.resolver()
        
        with self.assertRaises(TestDefinitionResolver.TestResolutionException):
            resolver.repoReferencesFor("repo0", "c0")
        

    def test_includes_can_share_environments(self):
        envdef = textwrap.dedent("""
            looper_version: 5
            environments:
              e: 
                platform: linux
                image:
                  dockerfile_contents: hi
            tests:
              t1/e:
                command: "./script.py 1"
            """)

        envdef2 = textwrap.dedent("""
            looper_version: 5
            tests:
              t2/e:
                command: "./script.py 1"
            """)

        repo = textwrap.dedent("""
            looper_version: 5
            includes:
              - ./envdef.yml
              - ./envdef2.yml
            """
            )

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/base", [], None)
        harness.manager.source_control.addCommit("repo0/base2", [], None)

        harness.manager.source_control.addCommit("repo0/c0", [], repo, {"envdef.yml": envdef, "envdef2.yml": envdef2})

        resolver = harness.resolver()
        
        self.assertEqual(sorted(resolver.testDefinitionsFor("repo0", "c0")), ["t1/e", "t2/e"])
        
    def test_includes_use_correct_repo(self):
        envdef = textwrap.dedent("""
            looper_version: 5
            environments:
              e: 
                platform: linux
                image:
                  dockerfile_contents: hi
            tests:
              t1/e:
                command: "./script.py 1"
            """)

        envdef2 = textwrap.dedent("""
            looper_version: 5
            tests:
              t2/e:
                command: "./script.py 1"
            """)

        repo = textwrap.dedent("""
            looper_version: 5
            includes:
              - ./envdef.yml
              - ./envdef2.yml
            """
            )

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/base", [], None)
        harness.manager.source_control.addCommit("repo0/base2", [], None)

        harness.manager.source_control.addCommit("repo0/c0", [], repo, {"envdef.yml": envdef, "envdef2.yml": envdef2})

        resolver = harness.resolver()
        
        self.assertEqual(sorted(resolver.testDefinitionsFor("repo0", "c0")), ["t1/e", "t2/e"])
         
    def test_bad_include_preserves_pins(self):
        envdef = textwrap.dedent("""
            looper_version: 5
            environments:
              e: 
                platform: not_valid
                image:
                  dockerfile_contents: hi
            tests:
              t1/e:
                command: "./script.py 1"
            """)

        repo = textwrap.dedent("""
            looper_version: 5
            repos:
              r: 
                reference: repo0/c0
                branch: master
                auto: true
            includes:
              - r/envdef.yml
            """
            )

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], None, {"envdef.yml": envdef})
        harness.manager.source_control.addCommit("repo0/c1", ["repo0/c0"], None, {"envdef.yml": envdef.replace("not_valid","linux")})
        harness.manager.source_control.addCommit("repo0/test", [], repo)

        harness.manager.source_control.setBranch("repo0/master", "repo0/c0")
        harness.manager.source_control.setBranch("repo0/tester", "repo0/test")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        with harness.database.view():
            commit = harness.getCommit("repo0/test")
            self.assertEqual(len(commit.data.repos), 1)
            self.assertEqual(len(commit.data.tests), 0)

            branch = harness.database.Branch.lookupOne(reponame_and_branchname=("repo0","tester"))
            pins = harness.database.BranchPin.lookupAll(branch=branch)

            self.assertEqual(len(pins),1)
            self.assertTrue(pins[0].auto)

        harness.manager.source_control.setBranch("repo0/master", "repo0/c1")
        harness.markRepoListDirty()

        harness.consumeBackgroundTasks()

        with harness.database.view():
            commit = branch.head
            self.assertEqual(len(commit.data.repos), 1)
            self.assertEqual(len(commit.data.tests), 1)


        
    def test_environment_overrides(self):
        envdef = textwrap.dedent("""
            looper_version: 5
            environments:
              e: 
                platform: linux
                image:
                  dockerfile_contents: hi
                test_stages:
                  - command: preCommand
              e2:
                base: e
                test_stages:
                  - command: preCommand2
            tests:
              t1/e2:
                command: actualCommand
            """)

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], envdef)

        resolver = harness.resolver()
        
        test = resolver.testDefinitionsFor("repo0", "c0")["t1/e2"]

        self.assertEqual([s.command for s in test.stages], ["preCommand", "preCommand2", "actualCommand"])
        
    def test_configuration_override(self):
        envdef = textwrap.dedent("""
            looper_version: 5
            environments:
              e: 
                platform: linux
                image:
                  dockerfile_contents: hi
                test_stages:
                  - command: preCommand
                test_configuration: override_at_root
              e2:
                base: []
                test_configuration: override_at_mixin
            tests:
              t1:
                environment: e
                command: actualCommand
              t2:
                environment: e
                mixins: [e2]
                command: actualCommand
              t3:
                environment: e
                mixins: [e2]
                configuration: override_at_test_level
                command: actualCommand
            """)

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], envdef)

        resolver = harness.resolver()
        
        self.assertEqual(resolver.testDefinitionsFor("repo0", "c0")["t1"].configuration, "override_at_root")
        self.assertEqual(resolver.testDefinitionsFor("repo0", "c0")["t2"].configuration, "override_at_mixin")
        self.assertEqual(resolver.testDefinitionsFor("repo0", "c0")["t3"].configuration, "override_at_test_level")
        
    def test_prioritization_filters(self):
        envdef = textwrap.dedent("""
            looper_version: 5
            environments:
              e: 
                platform: linux
                image:
                  dockerfile_contents: hi
            tests:
              foreach:
                name: [t1, t2]
                env: [e1, e2]
              repeat:
                ${name}/${env}:
                  environment: e
                  command: cmd
            prioritize:
              - 't1/*'
              - '*/e2'
            """)

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], envdef)
        resolver = harness.resolver()
        
        tests = resolver.testDefinitionsFor("repo0", "c0").values()

        self.assertEqual(
            set([t.name for t in tests if not t.disabled]), 
            set(["t1/e1","t1/e2","t2/e2"])
            )

        
 
    def test_environment_mixins(self):
        envdef = textwrap.dedent("""
            looper_version: 5
            environments:
              e: 
                platform: linux
                image:
                  dockerfile_contents: hi
                variables:
                  v: e
                test_stages:
                  - command: preCommand
              e2:
                base: []
                test_stages:
                  - command: preCommand2
                variables:
                  v: e2
            tests:
              t1/e:
                mixins: [e2]
                command: actualCommand - v=${v}
            """)

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], envdef)

        resolver = harness.resolver()
        
        test = resolver.testDefinitionsFor("repo0", "c0")["t1/e"]

        self.assertEqual([s.command for s in test.stages], ["preCommand" ,"preCommand2", "actualCommand - v=e2"])
        
