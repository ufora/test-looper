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


basic_repo_text = """
looper_version: 5
environments:
  linux:
    platform: linux
    image:
      dockerfile_contents: ""
builds:
  build_with_stages:
    environment: linux
    stages:
    - command: "hi"
      artifacts:
      - name: first_A
        directory: whatever
      - name: first_B
        directory: whatever2
    - command: "hi"
      artifacts:
      - name: second_A
        directory: whatever 
  build_needing_first_A:
    environment: linux
    dependencies:
      first_A: build_with_stages/first_A
    command: "hi"
  build_needing_first_B:
    environment: linux
    dependencies:
      first_A: build_with_stages/first_A
      first_B: build_with_stages/first_B
    command: "hi"
"""

repo_with_bad_artifact = (
    basic_repo_text[:-1]
    + """
  build_needing_bad_artifact:
    environment: linux
    dependencies:
      first_A: build_with_stages/doesnt_exist
      first_B: build_with_stages/first_B
    command: "hi"
"""
)
repo_with_two_artifacts_and_same_name = (
    basic_repo_text[:-1]
    + """
  build_with_double:
    environment: linux
    stages:
    - command: "hi"
      artifacts:
      - name: second_A
        directory: whatever 
    - command: "hi"
      artifacts:
      - name: second_A
        directory: whatever
    command: "hi"
"""
)

repo_with_two_artifacts_of_which_one_is_unnamed = (
    basic_repo_text[:-1]
    + """
  build_with_double:
    environment: linux
    stages:
    - command: "hi"
      artifacts:
      - name: second_A
        directory: whatever 
    - command: "hi"
      artifacts:
      - directory: whatever 
"""
)


class TestManagerStageDependencies(unittest.TestCase):
    def test_recognizes_invalid_dep(self):
        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], repo_with_bad_artifact)

        try:
            harness.resolver().testDefinitionsFor("repo0", "c0")
            err = ""
        except Exception as e:
            err = str(e)

        self.assertTrue("Can't resolve artifact" in err, "error is " + repr(err))

    def test_recognizes_double_dep(self):
        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit(
            "repo0/c0", [], repo_with_two_artifacts_and_same_name
        )

        try:
            harness.resolver().testDefinitionsFor("repo0", "c0")
            err = ""
        except Exception as e:
            err = str(e)

        self.assertTrue(
            "defined artifact 'second_A' twice" in err, "error is " + repr(err)
        )

    def test_recognizes_double_dep_of_unnamed(self):
        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit(
            "repo0/c0", [], repo_with_two_artifacts_of_which_one_is_unnamed
        )

        try:
            harness.resolver().testDefinitionsFor("repo0", "c0")
            err = ""
        except Exception as e:
            err = str(e)

        self.assertTrue(
            "can only define the unnamed artifact if it defines no others" in err,
            "error is " + repr(err),
        )

    def test_basic_dependency(self):

        harness = TestManagerTestHarness.getHarness()

        harness.manager.source_control.addCommit("repo0/c0", [], basic_repo_text)

        harness.manager.source_control.setBranch("repo0/master", "repo0/c0")

        harness.markRepoListDirty()
        harness.consumeBackgroundTasks()

        harness.enableBranchTesting("repo0", "master")

        harness.consumeBackgroundTasks()

        # we should see two tests, one of which can be started
        with harness.database.view():
            test1 = harness.lookupTestByFullname(("repo0/c0/build_with_stages"))
            test2 = harness.lookupTestByFullname(("repo0/c0/build_needing_first_A"))
            test3 = harness.lookupTestByFullname(("repo0/c0/build_needing_first_B"))

        self.assertTrue(test1)
        self.assertTrue(test2)
        self.assertTrue(test3)

        tests = harness.startAllNewTests()
        self.assertFalse(harness.startAllNewTests())

        self.assertTrue(len(tests) == 1, tests)

        testId = tests[0][0]
        with self.assertRaises(Exception):
            harness.manager.recordTestArtifactUploaded(
                testId, "not_first_A", harness.timestamp, isCumulative=False
            )

        harness.manager.recordTestArtifactUploaded(
            testId, "first_A", harness.timestamp, isCumulative=False
        )
        harness.manager.recordTestArtifactUploaded(
            testId, ["first_A"], harness.timestamp, isCumulative=True
        )
        harness.consumeBackgroundTasks()

        tests2 = harness.startAllNewTests()
        self.assertTrue(len(tests2) == 1)

        with harness.database.view():
            self.assertEqual(test1.activeRuns, 1)
            self.assertEqual(test2.activeRuns, 1)

        with self.assertRaises(Exception):
            harness.manager.recordTestArtifactUploaded(
                testId, ["first_A", "third"], harness.timestamp, isCumulative=True
            )

        harness.manager.recordTestArtifactUploaded(
            testId, ["first_A", "first_B"], harness.timestamp, isCumulative=True
        )
        harness.consumeBackgroundTasks()
        harness.startAllNewTests()

        with harness.database.view():
            self.assertEqual(test1.activeRuns, 1)
            self.assertEqual(test2.activeRuns, 1)
            self.assertEqual(test3.activeRuns, 1)
