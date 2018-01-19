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
import test_looper.data_model.VariableSubstitution as VariableSubstitution
import unittest
import yaml

basic_yaml_file = """
looper_version: 2
repos:
  child: child-repo-name/repo_hash
environments:
  linux: 
    base: child/linux
  windows: 
    base: child/windows
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

foreach_and_squash_yaml = """
foreach:
  - squash: {group: G1, prerequisites: P1}
    over:
    - {name: T1, tests_to_run: T1.test}
    - {name: T2, tests_to_run: T2.test}
  - squash: {group: G2, prerequisites: P2}
    over: 
    - {name: T3, tests_to_run: T3.test}
    - {name: T4, tests_to_run: T4.test}
repeat:
  "test/${group}/${name}": "${prerequisites} ${tests_to_run}"
"""

foreach_2_yml = """
tests:
  foreach:
    - {platform: platform-1}
    - {platform: platform-2}
  repeat:
    "test/all/${platform}":
      command: |
        cmd in ${platform}
"""

def apply_and_merge(vars, extras=None):
    return VariableSubstitution.apply_variable_substitutions_and_merge_repeatedly(vars, extras or {})

class TestDefinitionScriptTests(unittest.TestCase):
    def test_basic(self):
        tests, environments = TestDefinitionScript.extract_tests_from_str("repo", "hash", ".yml", basic_yaml_file)

        for name in ['build/linux', 'build/test_linux', 'test/linux', 'test/test_linux']:
            self.assertTrue(name in tests, name)

    def test_disallow_circular(self):
        try:
            TestDefinitionScript.extract_tests_from_str("repo", "hash", ".yml", circular_yaml_file)
            self.assertTrue(False)
        except Exception as e:
            self.assertTrue("circular" in str(e), e)

    def test_expansion(self):
        res = TestDefinitionScript.expand_macros(
          {"foreach": [
            {"name": 10, "hello": 20}, 
            {"name": 20, "hello": 30}
            ],
           "repeat": {
            "${name}_X": {"z": "${hello}"},
            "${name}_Y": {"b": "${hello}"},
            }
          }, {})

        self.assertEqual(
          res,
          {"10_X": {"z": "20"},
           "10_Y": {"b": "20"},
           "20_X": {"z": "30"},
           "20_Y": {"b": "30"}
           }
          )

    def test_expansion_and_replacement(self):
        res = TestDefinitionScript.expand_macros(
          {"define": {"name": [20, 30], "hello": 30},
           "in": ["a thing", "${name}"]
          }, {})

        self.assertEqual(
          res, ["a thing", [20, 30]]
          )

    def test_merging(self):
        res = TestDefinitionScript.expand_macros(
          {"define": {"name": [20, 30], "name2": [1,2]},
           "in": [
            {"merge": ["${name}", "${name2}"]},
            {"merge": [{"a": "${name}"}, {"b": "${name2}"}]}
            ]
          }, {})

        self.assertEqual(
          res, [[20,30,1,2], {"a": [20,30], "b": [1,2]}]
          )

    def test_cross_foreach(self):
        res = TestDefinitionScript.expand_macros(
          {"foreach": {"name": [20, 30], "name2": [1,2]},
           "repeat": {"${name}-${name2}": "hi"}
          }, {})

        self.assertEqual(
          res,
            {"20-1": "hi",
             "20-2": "hi",
             "30-1": "hi",
             "30-2": "hi"}
          )

    def test_squashing(self):
        res = TestDefinitionScript.expand_macros(yaml.load(foreach_and_squash_yaml), {})
        
        self.assertEqual(
          res, {
            'test/G1/T1': 'P1 T1.test',
            'test/G1/T2': 'P1 T2.test',
            'test/G2/T3': 'P2 T3.test',
            'test/G2/T4': 'P2 T4.test'
          })


    def test_variable_substitution(self):
        self.assertEqual(apply_and_merge({"A": "B"}), {"A": "B"})
        self.assertEqual(apply_and_merge({"A": "${B}"}), {"A": "${B}"})
        self.assertEqual(apply_and_merge({"A": "${B}", "B": "C"}), {"A": "C", "B": "C"})
        self.assertEqual(apply_and_merge({"A": "${B}"}, {"B": "C"}), {"A": "C", "B": "C"})

    def test_variable_chains_and_cycles(self):
        chain = {}
        for i in xrange(20):
          chain["A_%s" % i] = "${A_%s}_" % (i+1)
        chain["A_20"] = ""
        chain_merged = apply_and_merge(chain)

        self.assertEqual(chain_merged["A_19"], "_")
        self.assertEqual(chain_merged["A_10"], "_" * 10)
        self.assertEqual(chain_merged["A_0"], "_" * 20)

        chain["A_10"] = "${A_3}"

        with self.assertRaises(Exception):
            apply_and_merge(chain)

    def test_variable_sublookup(self):
        self.assertEqual(apply_and_merge({"A": "AB", "B": "BV", "ABBV": "FINAL", "D": "${${A}${B}}"})["D"], "FINAL")


