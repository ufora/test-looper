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
            looper_version: 2
            environments:
              ${env_name}: 
                platform: linux
                image:
                  dockerfile_contents: hi
                variables:
                  ${vname}: ${vdef}
            """)

        repo = textwrap.dedent("""
            looper_version: 2
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
            looper_version: 2
            repos:
              r: repo0/base
            """)

        envdef = textwrap.dedent("""
            looper_version: 2
            includes:
              - ./envdef2.yml
            """)

        repo = textwrap.dedent("""
            looper_version: 2
            includes:
              - ./envdef.yml
            """
            )

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], repo, {"envdef.yml": envdef, "envdef2.yml": envdef2})

        resolver = harness.resolver()
        
        self.assertEqual(sorted(resolver.repoReferencesFor("repo0", "c0").keys()), ["r"])

    def test_recursive_includes(self):
        envdef2 = textwrap.dedent("""
            looper_version: 2
            includes:
              - ./envdef.yml
            """)

        envdef = textwrap.dedent("""
            looper_version: 2
            includes:
              - ./envdef2.yml
            """)

        repo = textwrap.dedent("""
            looper_version: 2
            includes:
              - ./envdef.yml
            """
            )

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], repo, {"envdef.yml": envdef, "envdef2.yml": envdef2})

        resolver = harness.resolver()
        
        self.assertEqual(sorted(resolver.repoReferencesFor("repo0", "c0").keys()), [])

    def test_recursive_includes_with_variables_that_expand_forever(self):
        envdef2 = textwrap.dedent("""
            looper_version: 2
            includes:
              - path: ./envdef.yml
                variables:
                  var: v_${var}
            """)

        envdef = textwrap.dedent("""
            looper_version: 2
            includes:
              - path: ./envdef2.yml
                variables:
                  var: v_${var}
            """)

        repo = textwrap.dedent("""
            looper_version: 2
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
            looper_version: 2
            repos:
              r: repo0/base
            """)

        envdef = textwrap.dedent("""
            looper_version: 2
            repos:
              r: repo0/base2
            """)

        repo = textwrap.dedent("""
            looper_version: 2
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
            looper_version: 2
            environments:
              e: 
                platform: linux
                image:
                  dockerfile_contents: hi
            """)

        envdef = textwrap.dedent("""
            looper_version: 2
            environments:
              e: 
                platform: linux
                image:
                  dockerfile_contents: hi
            """)

        repo = textwrap.dedent("""
            looper_version: 2
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
            looper_version: 2
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
            looper_version: 2
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
            looper_version: 2
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
            looper_version: 2
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
            looper_version: 2
            tests:
              t2/e:
                command: "./script.py 1"
            """)

        repo = textwrap.dedent("""
            looper_version: 2
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
        

