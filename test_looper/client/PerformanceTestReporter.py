"""
PerformanceTestReporter

Allows test programs to report back performance data to a test-script runner. Test data is passed
in test files. The location of the files is passed to client programs using environment variables.
"""

import os
import simplejson
import time
import inspect
import warnings

TEST_DATA_LOCATION_ENVIRONMENT_VARIABLE = "TEST_LOOPER_PERFORMANCE_TEST_RESULTS_FILE"

class TimeoutException(Exception):
    pass

def isCurrentlyTesting():
    return os.getenv(TEST_DATA_LOCATION_ENVIRONMENT_VARIABLE) is not None

def record_test(testName, elapsedTime, metadata, **kwargs):
    if not (isinstance(elapsedTime, float) or elapsedTime is None):
        warnings.warn(
            "We may only record a float, or None (in case of failure) for elapsed time"
            )
        return

    if not isCurrentlyTesting():
        warnings.warn(
            ("We are not currently testing, so we can't record test results. "
             "Set the environment variable %s to point to a valid path.") % \
            TEST_DATA_LOCATION_ENVIRONMENT_VARIABLE
            )
        return

    targetPath = os.getenv(TEST_DATA_LOCATION_ENVIRONMENT_VARIABLE)

    perfLogEntry = {
        "name": str(testName),
        "time": elapsedTime if isinstance(elapsedTime, float) else None,
        "metadata": metadata
        }
    perfLogEntry.update(kwargs)

    with open(targetPath, "ab+") as f:
        f.write(simplejson.dumps(perfLogEntry) + "\n")

def recordThroughputTest(testName, runtime, n, baseMultiplier, metadata):
    record_test(testName,
                runtime / n * baseMultiplier,
                metadata,
                n=n,
                baseMultiplier=baseMultiplier,
                actualTime=runtime)

def testThroughput(testName,
                   testFunOfN,
                   setupFunOfN=None,
                   transformOfN=None,
                   metadata=None,
                   maxNToSearch=1000000,
                   baseMultiplier=1,
                   timeoutInSec=30):
    counter = 0
    n = counter
    runtime = None

    timeUsed = 0

    while timeUsed < timeoutInSec and counter <= maxNToSearch:
        try:
            counter += 1

            n = counter
            if transformOfN is not None:
                n = transformOfN(counter)

            if setupFunOfN is not None:
                setupFunOfN(n)

            t0 = time.time()
            testFunOfN(n)
            runtime = time.time() - t0

            timeUsed += runtime

        except TimeoutException:
            break

    assert runtime is not None # we had at least one passing result before timing out

    if isCurrentlyTesting():
        recordThroughputTest(testName, runtime, n, baseMultiplier, metadata)

def loadTestsFromFile(testFileName):
    with open(testFileName, "rb") as f:
        return [simplejson.loads(x) for x in f.readlines()]

def perftest(test_name):
    """Decorate a unit-test so that it records performance in the global test database"""
    def decorator(f):
        meta = {
            'file': "/".join(inspect.getmodule(f).__name__.split(".")) + ".py",
            'line': inspect.getsourcelines(f)[1]
            }

        def innerTestFun(self):
            t0 = time.time()

            try:
                result = f(self)
            except:
                if isCurrentlyTesting():
                    record_test(test_name, None, meta)
                raise

            if isCurrentlyTesting():
                record_test(test_name, time.time() - t0, meta)

            return result

        innerTestFun.__name__ = f.__name__
        return innerTestFun

    return decorator

def getCurrentStackframeFileAndLine(framesAbove):
    curStack = inspect.currentframe()
    above = inspect.getouterframes(curStack)
    twoAbove = above[framesAbove][0]

    return {
        'file': "/".join(inspect.getmodule(twoAbove).__name__.split(".")) + ".py",
        'line': inspect.getsourcelines(twoAbove)[1]
        }
