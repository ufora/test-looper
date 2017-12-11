#   Copyright 2017 Braxton Mckee
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import unittest

basic_yaml_file = """
looper_version: 2
repos:
  child: child-repo-name/repo_hash
environments:
  linux: 
    import: child/linux
  windows: 
    import: child/windows
  test_linux:
    platform: linux
    image:
      dockerfile: "test_looper/Dockerfile.txt"
    variables:
      ENV_VAR: ENV_VAL
  all_linux:
    group: [linux, test_linux]
builds:
  build/all_linux:
    command: "build.sh $TEST_LOOPER_IMPORTS/child"
    dependencies:
      child: child/build/
tests:
  test/all_linux:
    command: "test.sh $TEST_LOOPER_IMPORTS/build"
    dependencies:
      build: build/
"""

circular_yaml_file = """
looper_version: 2
environments:
  linux:
    platform: linux
    image:
      dockerfile: "test_looper/Dockerfile.txt"
builds:
  build1/linux:
    command: "build.sh"
    dependencies:
      child: build2/linux
  build2/linux:
    command: "build.sh"
    dependencies:
      child: build1/linux
"""


class TestDefinitionScriptTests(unittest.TestCase):
    def test_basic(self):
        tests = TestDefinitionScript.extract_tests_from_str("repo/hash", "yaml", basic_yaml_file)

        for name in ['build/linux', 'build/test_linux', 'test/linux', 'test/test_linux']:
            self.assertTrue(name in tests, name)

    def test_disallow_circular(self):
        try:
            TestDefinitionScript.extract_tests_from_str("repo/hash", "yaml", circular_yaml_file)
            self.assertTrue(False)
        except Exception as e:
            self.assertTrue("circular" in str(e), e)
