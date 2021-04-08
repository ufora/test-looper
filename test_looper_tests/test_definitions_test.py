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

import test_looper.data_model.TestDefinition as TestDefinition
import test_looper.data_model.TestDefinitionScript as TestDefinitionScript
import test_looper.data_model.VariableSubstitution as VariableSubstitution
import unittest
import textwrap
import yaml

basic_yaml_file = """
looper_version: 5
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
      AN_INT_VAR: 10
      A_BOOL_VAR: true
builds:
  foreach: {env: [linux, test_linux]}
  repeat:
    build/${env}:
      command: "src/build.sh $TEST_INPUTS/child"
      dependencies:
        src: HEAD
        child: child/build/${env}
tests:
  foreach: {env: [linux, test_linux]}
  repeat:
    test/${env}:
      command: "src/test.sh $TEST_INPUTS/build"
      dependencies:
        src: HEAD
        build: build/${env}
"""

circular_yaml_file = """
looper_version: 5
environments:
  linux:
    platform: linux
    image:
      dockerfile: "test_looper/Dockerfile.txt"
builds:
  build1/linux:
    command: "src/build.sh"
    dependencies:
      src: HEAD
      child: build2/linux
  build2/linux:
    command: "src/build.sh"
    dependencies:
      src: HEAD
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

environment_yaml_file = """
environments:
looper_version: 5
environments:
  env_root:
    platform: windows
    image:
      base_ami: ami-123
  env_dep:
    base: env_root
    setup_script_contents: |
      TestFileContents
  child1:
    base: env_dep
    setup_script_contents: |
      ChildContents
  child2:
    base: env_dep
  diamond:
    base: [child1, child2]
"""

includes_yaml_file = """
looper_version: 5
includes:
  foreach:
    v1: [v1_val1, v1_val2]
    v2: [v2_val1, v2_val2]
  repeat:
    - repo1/path
    - path: repo2/path
      variables:
        v3: v3_val1
        v4: v4_val1
"""


def apply_and_merge(vars, extras=None):
    return VariableSubstitution.apply_variable_substitutions_and_merge_repeatedly(
        vars, extras or {}
    )


class TestDefinitionScriptTests(unittest.TestCase):
    def test_basic(self):
        tests, environments, repos, includes, test_sets, triggers = TestDefinitionScript.extract_tests_from_str(
            "repo", "hash", ".yml", basic_yaml_file
        )

        for name in [
            "build/linux",
            "build/test_linux",
            "test/linux",
            "test/test_linux",
        ]:
            self.assertTrue(name in tests, name)

        self.assertEqual(
            set(environments["test_linux"].variables),
            set(["ENV_VAR", "AN_INT_VAR", "A_BOOL_VAR"]),
        )
        self.assertEqual(environments["test_linux"].variables["AN_INT_VAR"], "10")
        self.assertEqual(environments["test_linux"].variables["A_BOOL_VAR"], "true")

    def test_environment_inheritance(self):
        tests, environments, repos, includes, test_sets, triggers = TestDefinitionScript.extract_tests_from_str(
            "repo", "hash", ".yml", environment_yaml_file
        )

        Ref = TestDefinition.EnvironmentReference.Reference

        deps = {
            Ref(repo="repo", commitHash="hash", name=n): environments[n]
            for n in environments
        }

        env = environments["diamond"]

        env = TestDefinition.merge_environments(env, deps)

        self.assertEqual(env.environment_name, "diamond")
        self.assertEqual(env.inheritance, ("child2", "child1", "env_dep", "env_root"))
        self.assertEqual(
            env.image.setup_script_contents, "\nTestFileContents\n\nChildContents\n"
        )

    def test_includes_and_variables(self):
        _, _, _, includes, _, _ = TestDefinitionScript.extract_tests_from_str(
            "repo", "hash", ".yml", includes_yaml_file
        )

        self.assertTrue(len(includes) == 8)

        res = []
        for i in includes:
            v = {"path": i.path}
            v.update(i.variables)
            res.append(v)

        computedRes = []
        for v1 in ["v1_val1", "v1_val2"]:
            for v2 in ["v2_val1", "v2_val2"]:
                computedRes.append({"path": "repo1/path", "v1": v1, "v2": v2})
                computedRes.append(
                    {
                        "path": "repo2/path",
                        "v1": v1,
                        "v2": v2,
                        "v3": "v3_val1",
                        "v4": "v4_val1",
                    }
                )

        self.assertEqual(res, computedRes)

    def test_expansion(self):
        res = TestDefinitionScript.MacroExpander().expand_macros(
            {
                "foreach": [{"name": 10, "hello": 20}, {"name": 20, "hello": 30}],
                "repeat": {
                    "${name}_X": {"z": "${hello}"},
                    "${name}_Y": {"b": "${hello}"},
                },
            },
            {},
        )

        self.assertEqual(
            res,
            {
                "10_X": {"z": "20"},
                "10_Y": {"b": "20"},
                "20_X": {"z": "30"},
                "20_Y": {"b": "30"},
            },
        )

    def test_expansion_and_replacement(self):
        res = TestDefinitionScript.MacroExpander().expand_macros(
            {"define": {"name": [20, 30], "hello": 30}, "in": ["a thing", "${name}"]},
            {},
        )

        self.assertEqual(res, ["a thing", [20, 30]])

    def test_merging(self):
        res = TestDefinitionScript.MacroExpander().expand_macros(
            {
                "define": {"name": [20, 30], "name2": [1, 2]},
                "in": [
                    {"merge": ["${name}", "${name2}"]},
                    {"merge": [{"a": "${name}"}, {"b": "${name2}"}]},
                ],
            },
            {},
        )

        self.assertEqual(res, [[20, 30, 1, 2], {"a": [20, 30], "b": [1, 2]}])

    def test_cross_foreach(self):
        res = TestDefinitionScript.MacroExpander().expand_macros(
            {
                "foreach": {"name": [20, 30], "name2": [1, 2]},
                "repeat": {"${name}-${name2}": "hi"},
            },
            {},
        )

        self.assertEqual(res, {"20-1": "hi", "20-2": "hi", "30-1": "hi", "30-2": "hi"})

    def test_squashing(self):
        res = TestDefinitionScript.MacroExpander().expand_macros(
            yaml.load(foreach_and_squash_yaml), {}
        )

        self.assertEqual(
            res,
            {
                "test/G1/T1": "P1 T1.test",
                "test/G1/T2": "P1 T2.test",
                "test/G2/T3": "P2 T3.test",
                "test/G2/T4": "P2 T4.test",
            },
        )

    def test_casing(self):
        case_yaml = textwrap.dedent(
            """
            define:
              lookup_table:
                a: A_res
                b: B_res
                c: C_res
            in:
              - case: a
                in: ${lookup_table}
              - case: c
                in: ${lookup_table}
              - case: b
                in: ${lookup_table}
            """
        )

        res = TestDefinitionScript.MacroExpander().expand_macros(
            yaml.load(case_yaml), {}
        )

        self.assertEqual(res, ["A_res", "C_res", "B_res"])

    def test_variable_substitution(self):
        self.assertEqual(apply_and_merge({"A": "B"}), {"A": "B"})
        self.assertEqual(apply_and_merge({"A": "${B}"}), {"A": "${B}"})
        self.assertEqual(apply_and_merge({"A": "${B}", "B": "C"}), {"A": "C", "B": "C"})
        self.assertEqual(
            apply_and_merge({"A": "${B}"}, {"B": "C"}), {"A": "C", "B": "C"}
        )

    def test_variable_chains_and_cycles(self):
        chain = {}
        for i in range(20):
            chain["A_%s" % i] = "${A_%s}_" % (i + 1)
        chain["A_20"] = ""
        chain_merged = apply_and_merge(chain)

        self.assertEqual(chain_merged["A_19"], "_")
        self.assertEqual(chain_merged["A_10"], "_" * 10)
        self.assertEqual(chain_merged["A_0"], "_" * 20)

        chain["A_10"] = "${A_3}"

        with self.assertRaises(Exception):
            apply_and_merge(chain)

    def test_variable_sublookup(self):
        self.assertEqual(
            apply_and_merge(
                {"A": "AB", "B": "BV", "ABBV": "FINAL", "D": "${${A}${B}}"}
            )["D"],
            "FINAL",
        )
