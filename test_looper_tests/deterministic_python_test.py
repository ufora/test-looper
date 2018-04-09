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

import test_looper.deterministic_python.interpreter as interpreter
import unittest
import textwrap

class DeterministicPythonTests(unittest.TestCase):
    def test_basic(self):
        i = interpreter.Interpreter()
        i.interpretModule(textwrap.dedent("""
            print 'hi',
            print 'hi2'
            """))
        self.assertEqual(i.printResults, ["hi", "hi2\n"])

    def test_assignment(self):
        i = interpreter.Interpreter()
        i.interpretModule(textwrap.dedent("""
            x = 'hi'
            print x
            """))
        self.assertEqual(i.printResults, ["hi\n"])

    def test_arithmetic(self):
        i = interpreter.Interpreter()
        i.interpretModule(textwrap.dedent("""
            x = 'hi'
            print x+x
            """))
        self.assertEqual(i.printResults, ["hihi\n"])

    def test_comparison(self):
        i = interpreter.Interpreter()
        i.interpretModule(textwrap.dedent("""
            print True and False,
            print True or False,
            print False or False,
            """))
        self.assertEqual(i.printResults, ["False", "True", "False"])
