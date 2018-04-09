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

"""

implement a 'deterministic' python interpreter (in python, so yes it's slow).
Our goal is to provide a subset of python that's rich enough to be a useful
configuration tool but that's deterministic from run to run (e.g. no 'id', stable
sort order of items in dicts) and that's secure (no ability to modify the host
environment, run out of memory, etc)

"""

import test_looper.deterministic_python.python_ast as python_ast
import test_looper.deterministic_python.values as values

class StackFrame:
    def __init__(self, enclosingScope):
        self.values = {}
        self.enclosingScope = enclosingScope

    def lookupVariable(self, varname):
        if varname in self.values:
            return self.values[varname]
        if self.enclosingScope:
            return self.enclosingScope.lookupVariable(varname)
        return None

    def assign(self, varname, value):
        assert isinstance(value, values.Value)
        self.values[varname] = value

class TerminalState:
    def __init__(self, value):
        self.value = value

class ModuleInterpreterState:
    def __init__(self, stackframe, module, parentState):
        self.stackframe = stackframe
        self.statements = module.body
        self.curStatementIx = 0
        self.parentState = None

    def stepForward(self, interpreter):
        curStatement = self.statements[self.curStatementIx]

        return interpreter.createStatementState(self, self.stackframe, curStatement)

    def resumeWithValue(self, interpreter, value):
        if value is None:
            self.curStatementIx += 1

            if self.curStatementIx >= len(self.statements):
                value = values.NoneValue()

        if value is not None:
            if self.parentState:
                return self.parentState.resumeWithValue(value)
            else:
                return TerminalState(value)

        return self

class ReturnStatementState:
    def __init__(self, parentState, stackframe, curStatement):
        self.parentState = parentState
        self.stackframe = stackframe
        self.curStatement = curStatement

    def stepForward(self, interpreter):
        if self.curStatement.value.matches.Null:
            return self.parentState.resumeWithValue(interpreter, values.NoneValue())
        else:
            return interpreter.createExpressionState(self, self.stackframe, curStatement.value.val)

    def resumeWithValue(self, interpreter, value):
        self.parentState.resumeWithValue(interpreter, value)

class StrExpressionState:
    def __init__(self, parentState, stackframe, expr):
        self.expr = expr
        self.parentState = parentState

    def stepForward(self, interpreter):
        return self.parentState.resumeWithValue(interpreter, values.StringValue(self.expr.s))

class NumExpressionState:
    def __init__(self, parentState, stackframe, expr):
        self.expr = expr
        self.parentState = parentState

    def stepForward(self, interpreter):
        if self.expr.n.matches.Int:
            v = values.IntValue(self.expr.n)
        elif self.expr.n.matches.Boolean:
            v = values.BoolValue(self.expr.n)
        elif self.expr.n.matches.Float:
            v = values.FloatValue(self.expr.n)
        elif self.expr.n.matches.None:
            v = values.NoneValue()
        elif self.expr.n.matches.Long:
            v = values.LongValue(long(self.expr.n))
        else:
            interpreter.raiseUnsupportedOperationException("Invalid constant", self.expr)

        return self.parentState.resumeWithValue(interpreter, v)

class NameExpressionState:
    def __init__(self, parentState, stackframe, expr):
        self.expr = expr
        self.stackframe = stackframe
        self.parentState = parentState

    def stepForward(self, interpreter):
        varname = self.expr.id
        
        val = self.stackframe.lookupVariable(varname)
        if not val:
            interpreter.raiseUnsupportedOperationException("Unbound variable " + varname, self.expr)

        return self.parentState.resumeWithValue(interpreter, val)

class BinOpExpressionState:
    def __init__(self, parentState, stackframe, expr):
        self.expr = expr
        self.stackframe = stackframe
        self.parentState = parentState
        self.values = [None, None]

    def stepForward(self, interpreter):
        if not self.values[0]:
            return interpreter.createExpressionState(self, self.stackframe, self.expr.left)
        if not self.values[1]:
            return interpreter.createExpressionState(self, self.stackframe, self.expr.right)

        return self.values[0].binaryOp(interpreter, self.parentState, self.expr.op, self.values[1])

    def resumeWithValue(self, interpreter, val):
        if not self.values[0]:
            self.values[0] = val
        else:
            assert not self.values[1]
            self.values[1] = val

        return self

class BoolOpExpressionState:
    def __init__(self, parentState, stackframe, expr):
        self.expr = expr
        self.stackframe = stackframe
        self.parentState = parentState
        self.valuesChecked = 0

    def stepForward(self, interpreter):
        return interpreter.createExpressionState(self, self.stackframe, self.expr.values[0])

    def resumeWithValue(self, interpreter, val):
        if not isinstance(val, values.BoolValue):
            return values.BoolTypeValue().call(interpreter, self, [val])

        #short-circuit false
        if self.expr.op.matches.And and not val.pyValue:
            return self.parentState.resumeWithValue(interpreter, val)

        #short-circuit true
        if self.expr.op.matches.Or and val.pyValue:
            return self.parentState.resumeWithValue(interpreter, val)

        self.valuesChecked += 1

        if self.valuesChecked >= len(self.expr.values):
            return self.parentState.resumeWithValue(interpreter, val)
        else:
            return interpreter.createExpressionState(self, self.stackframe, self.expr.values[self.valuesChecked])

class ComparisonOpExpressionState:
    def __init__(self, parentState, stackframe, expr):
        self.expr = expr
        self.stackframe = stackframe
        self.parentState = parentState
        self.curLeftVal = None
        self.curRightVal = None
        self.curIx = 0

    def stepForward(self, interpreter):
        return interpreter.createExpressionState(self, self.stackframe, self.expr.left)

    def resumeWithValue(self, interpreter, val):
        if self.curLeftVal is None:
            self.curLeftVal = val
            assert self.curIx == 0
            return interpreter.createExpressionState(self, self.stackframe, self.expr.comparators[0])

        if self.curRightVal is None:
            self.curRightVal = val
            return self.curLeftVal.compareOp(interpreter, self, self.expr.ops[self.curIx], self.curRightVal)

        if not isinstance(val, values.BoolValue):
            interpreter.raiseUnsupportedOperationException("Comparison returned non-bool")

        if not val.pyValue:
            return self.parentState.resumeWithValue(interpreter, val)

        if self.curIx + 1 >= len(self.expr.ops):
            return self.parentState.resumeWithValue(interpreter, val)

        self.curIx += 1
        self.curLeftVal = self.curRightVal
        self.curRightVal = None

        return interpreter.createExpressionState(self, self.stackframe, self.expr.comparators[self.curIx])

class UnaryOpExpressionState:
    def __init__(self, parentState, stackframe, expr):
        self.expr = expr
        self.stackframe = stackframe
        self.parentState = parentState
        self.values = [None]

    def stepForward(self, interpreter):
        if not self.values[0]:
            return interpreter.createExpressionState(self, self.stackframe, self.expr.operand)
        
        return self.values[0].unaryOp(interpreter, self.parentState, self.expr.op)

    def resumeWithValue(self, interpreter, val):
        if not self.values[0]:
            self.values[0] = val
        else:
            assert not self.values[1]
            self.values[1] = val

        return self

class AssignStatementState:
    def __init__(self, parentState, stackframe, statement):
        self.parentState = parentState
        self.stackframe = stackframe
        self.statement = statement
        self.value = None
        self.pendingStringification = None

    def stepForward(self, interpreter):
        if len(self.statement.targets) > 1 or not self.statement.targets[0].matches.Name:
            interpreter.raiseUnsupportedOperationException("Complex assignment not supported", self.statement)

        return interpreter.createExpressionState(self, self.stackframe, self.statement.value)

    def resumeWithValue(self, interpreter, value):
        name = self.statement.targets[0].id
        self.stackframe.assign(name, value)

        return self.parentState.resumeWithValue(interpreter, None)

class PrintStatementState:
    def __init__(self, parentState, stackframe, printStatement):
        self.parentState = parentState
        self.stackframe = stackframe
        self.printStatement = printStatement
        self.valuesSoFar = []
        self.pendingStringification = False

    def stepForward(self, interpreter):
        if self.printStatement.dest.matches.Value:
            interpreter.raiseUnsupportedOperationException("Printing to file object not supported", self.printStatement)

        if len(self.valuesSoFar) < len(self.printStatement.values):
            return interpreter.createExpressionState(self, self.stackframe, self.printStatement.values[len(self.valuesSoFar)])

        interpreter.printResults.append(
            " ".join([v.pyValue for v in self.valuesSoFar]) + ("\n" if self.printStatement.nl else "")
            )

        return self.parentState.resumeWithValue(interpreter, None)

    def resumeWithValue(self, interpreter, value):
        if self.pendingStringification:
            if not isinstance(value, values.StringValue):
                interpreter.raiseUnsupportedOperationException("'str' didn't return a string")
            self.valuesSoFar.append(value)
            self.pendingStringification = False
            return self

        if not isinstance(value, values.StringValue):
            self.pendingStringification = True
            return values.StringTypeValue().call(interpreter, self, [value])
        else:
            self.valuesSoFar.append(value)

        return self


_expressionStateTypeLookup = {
    "Str": StrExpressionState,
    "Num": NumExpressionState,
    "Name": NameExpressionState,
    "BinOp": BinOpExpressionState,
    "UnaryOp": UnaryOpExpressionState,
    "BoolOp": BoolOpExpressionState,
    "Compare": ComparisonOpExpressionState
    }

_statementStateTypeLookup = {
    "Return": ReturnStatementState,
    "Print": PrintStatementState,
    "Assign": AssignStatementState
    }

class Interpreter(object):
    def __init__(self):
        self.printResults = []

    def raiseUnsupportedOperationException(self, msg, astObject):
        raise Exception(msg)

    def createExpressionState(self, parentState, stackframe, expr):
        stateType = _expressionStateTypeLookup.get(expr._which)
        if not stateType:
            self.raiseUnsupportedOperationException("Unsupported expression type %s" % expr._which, expr)
        return stateType(parentState, stackframe, expr)

    def createStatementState(self, parentState, stackframe, statement):
        stateType = _statementStateTypeLookup.get(statement._which)
        if not stateType:
            self.raiseUnsupportedOperationException("Unsupported statement type %s" % statement._which, expr)
        return stateType(parentState, stackframe, statement)

    def interpretModule(self, text):
        ast = python_ast.parse_module(text)

        frame = StackFrame(None)

        frame.assign("True", values.BoolValue(True))
        frame.assign("False", values.BoolValue(False))
        frame.assign("str", values.StringTypeValue())
        frame.assign("bool", values.BoolTypeValue())

        state = ModuleInterpreterState(frame, ast, None)

        while not isinstance(state, TerminalState):
            state = state.stepForward(self)

        return frame
