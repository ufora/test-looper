#   Copyright 2018 Braxton Mckee
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

class Value(object):
    def binaryOp(self, interpreter, continuation, op, rhs):
        interpreter.raiseUnsupportedOperationException("Not supported")

    def unaryOp(self, interpreter, continuation, op):
        interpreter.raiseUnsupportedOperationException("Not supported")

    def compareOp(self, interpreter, continuation, op, rhs):
        interpreter.raiseUnsupportedOperationException("Not supported")

    def call(self, interpreter, continuation, args):
        interpreter.raiseUnsupportedOperationException("Not supported")


class PrimitiveValue(Value):
    binopFuncs = {
        "Add": (lambda x,y: x+y),
        "Sub": (lambda x,y: x-y),
        "Mul": (lambda x,y: x*y),
        "Div": (lambda x,y: x/y),
        "Mod": (lambda x,y: x%y),
        "Pow": (lambda x,y: x**y),
        "LShift": (lambda x,y: x<<y),
        "RShift": (lambda x,y: x>>y),
        "BitOr": (lambda x,y: x|y),
        "BitXor": (lambda x,y: x^y),
        "BitAnd": (lambda x,y: x&y),
        "FloorDiv": (lambda x,y: x//y),
        }
    unopFuncs = {
        "Invert": (lambda x: ~ x),
        "Not": (lambda x: not x),
        "UAdd": (lambda x: +y),
        "USub": (lambda x: -x),
        }
    compareopFuncs = {
        "Eq": (lambda x,y: x == y),
        "NotEq": (lambda x,y: x != y),
        "Lt": (lambda x,y: x < y),
        "LtE": (lambda x,y: x <= y),
        "Gt": (lambda x,y: x > y),
        "GtE": (lambda x,y: x >= y),
        "Is": (lambda x,y: x is y),
        "IsNot": (lambda x,y: x is not y),
        "In": (lambda x,y: x in y),
        "NotIn": (lambda x,y: x not in y)
        }

    @staticmethod
    def createForValue(val):
        if val is None:
            return NoneValue()

        if isinstance(val, str):
            return StringValue(val)

        if isinstance(val, bool):
            return BoolValue(val)

        if isinstance(val, int):
            return IntValue(val)

        assert False

    def binaryOp(self, interpreter, continuation, op, rhs):
        if isinstance(rhs, PrimitiveValue):
            try:
                opFun = PrimitiveValue.binopFuncs[op._which]
                value = PrimitiveValue.createForValue(opFun(self.pyValue, rhs.pyValue))
            except:
                interpreter.raiseUnsupportedOperationException("Not supported", None)

            return continuation.resumeWithValue(interpreter, value)

        interpreter.raiseUnsupportedOperationException("Not supported", None)

    def unaryOp(self, interpreter, continuation, op):
        try:
            opFun = PrimitiveValue.unopFuncs[op._which]
            value = PrimitiveValue.createForValue(opFun(self.pyValue))
        except:
            interpreter.raiseUnsupportedOperationException("Not supported", None)

        return continuation.resumeWithValue(interpreter, value)

    def compareOp(self, interpreter, continuation, op, rhs):
        if isinstance(rhs, PrimitiveValue):
            try:
                opFun = PrimitiveValue.compareopFuncs[op._which]
                value = PrimitiveValue.createForValue(opFun(self.pyValue, rhs.pyValue))
            except:
                interpreter.raiseUnsupportedOperationException("Not supported", None)

            return continuation.resumeWithValue(interpreter, value)

        interpreter.raiseUnsupportedOperationException("Not supported", None)

class NoneValue(PrimitiveValue):
    """Python's None value."""
    pass

class StringValue(PrimitiveValue):
    """A string value"""
    def __init__(self, pyValue):
        self.pyValue = pyValue

class IntValue(PrimitiveValue):
    def __init__(self, pyValue):
        self.pyValue = pyValue

class BoolValue(PrimitiveValue):
    def __init__(self, pyValue):
        self.pyValue = pyValue
        
class FloatValue(PrimitiveValue):
    def __init__(self, pyValue):
        self.pyValue = pyValue

class LongValue(PrimitiveValue):
    def __init__(self, pyValue):
        self.pyValue = pyValue

class StringTypeValue(Value):
    """the 'str' object"""
    def __init__(self):
        pass

    def call(self, interpreter, continuation, args):
        if len(args) == 1 and isinstance(args[0], PrimitiveValue):
            return continuation.resumeWithValue(
                interpreter,
                PrimitiveValue.createForValue(str(args[0].pyValue))
                )
        interpreter.raiseUnsupportedOperationException("Not supported", None)

class BoolTypeValue(Value):
    """the 'str' object"""
    def __init__(self):
        pass

    def call(self, interpreter, continuation, args):
        if len(args) == 1 and isinstance(args[0], PrimitiveValue):
            return continuation.resumeWithValue(
                interpreter,
                PrimitiveValue.createForValue(bool(args[0].pyValue))
                )
        interpreter.raiseUnsupportedOperationException("Not supported", None)

class FunctionValue(Value):
    def __init__(self, functionDef, surroundingScope):
        self.functionDef = functionDef
        self.surroundingScope = surroundingScope
