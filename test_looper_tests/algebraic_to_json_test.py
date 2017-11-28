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

from test_looper.core.algebraic import Alternative, List, Nullable
from test_looper.core.algebraic_to_json import Encoder

import unittest

expr = Alternative("Expr")
expr.Constant = {'value': int}
expr.Add = {'l': expr, 'r': expr}
expr.Sub = {'l': expr, 'r': expr}
expr.Mul = {'l': expr, 'r': expr}
expr.Many = {'vals': List(expr)}
expr.Possibly = {'val': Nullable(expr)}

c10 = expr.Constant(value=10)
c20 = expr.Constant(value=20)
a = expr.Add(l=c10,r=c20)
several = expr.Many([c10, c20, a, expr.Possibly(None), expr.Possibly(c20)])

class AlgebraicToJsonTests(unittest.TestCase):
    def test_basic(self):
        e = Encoder()

        self.assertEqual(
            e.to_json(expr.Constant(value=10)), 
            {'type': "Constant", 'value': 10}
            )

        self.assertEqual(
            e.to_json(a),
            {'type': "Add", 
             "l": {'type': "Constant", 'value': 10},
             "r": {'type': "Constant", 'value': 20}
             }
            )

        self.assertEqual(
            e.to_json(expr.Possibly(None)),
            {'type': "Possibly", 'val': None}
            )


    def test_roundtrip(self):
        e = Encoder()

        for item in [c10, c20, a, several]:
            self.assertEqual(item, e.from_json(e.to_json(item), item._alternative))
