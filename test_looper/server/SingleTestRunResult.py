import test_looper.core.algebraic as algebraic

SingleTestRunResult = algebraic.Alternative("SingleTestRunResult")
SingleTestRunResult.Result = {
    'testName': str,
    'startTimestamp': algebraic.Nullable(float),
    'elapsed': algebraic.Nullable(float),
    'testSucceeded': bool,
    'hasLogs': bool,
    'testPassIx': int #how many times have we seen this test in this batch, starting with 0
    }

