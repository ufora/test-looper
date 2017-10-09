import os
import os.path
import tempfile
import shutil
import sys
import traceback
import threading
import multiprocessing
import logging

import test_looper.core.SubprocessingModified as subprocess
import test_looper.core.SubprocessRunner as SubprocessRunner
from test_looper.core.DirectoryScope import DirectoryScope

def removeFileIfExists(filePath):
    if os.path.isfile(filePath):
        os.remove(filePath)

def removeAndCreateDirectory(directory):
    if (os.path.isdir(directory)):
        shutil.rmtree(directory)
    os.mkdir(directory)

class TestScriptRunner(object):
    def __init__(self, testRoot, defaultTimeout = 500):
        logging.info("Initializing TestScriptRunner at %s", testRoot)

        self.testRoot = testRoot
        self.pathToProjectRoot = testRoot
        
        self.defaultTimeout = defaultTimeout
        self.scripts = self.findTestScripts_()

        self.getTimeouts_()

    def getTimeouts_(self):
        self.timeouts = {}

        def walker(arg, dirname, fnames):
            for f in fnames:
                if f.endswith(".py"):
                    relpath = os.path.relpath(os.path.join(dirname,f), self.testRoot)

                    if relpath not in self.timeouts:
                        self.timeouts[relpath] = self.defaultTimeout

        os.path.walk(self.testRoot, walker, None)

    def getTimeout(self, abspath):
        relpath = os.path.relpath(abspath, self.testRoot)

        return self.timeouts[relpath]

    def findTestScripts_(self):
        scripts = []
        def walker(arg, dirname, fnames):
            for f in fnames:
                if f.endswith(".py"):
                    scripts.append(os.path.join(dirname,f))

        os.path.walk(self.testRoot, walker, None)
        return scripts


    def run(self):
        self.envVars = self.createTestEnvironment_()
        return self.run_()

    def run_(self):
        assert self.envVars is not None

        try:
            return self.runScripts_()
        finally:
            self.mergeTestResultFiles_()

    def createTestEnvironment_(self):
        envVars = dict(os.environ)
        envVars["TESTROOT"] = self.testRoot
        envVars["TEST_ERROR_OUTPUT_DIRECTORY"] = self.pathToProjectRoot
        return envVars


    def runScripts_(self):
        scriptsThatFailed = []
        for s in self.scripts:
            if not self.runScript_(s):
                scriptsThatFailed.append(s)

        if scriptsThatFailed:
            print
            print
            print "SCRIPT FAILURE REPORT: ",
            print len(scriptsThatFailed), " scripts failed!"
            for s in scriptsThatFailed:
                print "\t", s
            return False

        return True

    def runScript_(self, script):
        print
        print "Running %s" % script
        print "with a timeout of ", self.getTimeout(script)

        if sys.platform == 'linux2':
            directory, filename = os.path.split(script)
            genCore = os.path.abspath('generateCore.gdb')
            args = [sys.executable, "-u", '-c', "print 'started'; execfile('%s')" % filename]

            with DirectoryScope(directory):
                tries = 0
                runner = None

                while tries < 5 and runner is None:
                    startedEvent = threading.Event()

                    def printOutput(line):
                        if line == 'started':
                            startedEvent.set()
                            print "Script %s started" % filename
                        else:
                            print "OUT> %s\n" % line,

                    def printErr(line):
                        print "ERR> %s\n" % line,

                    runner = SubprocessRunner.SubprocessRunner(
                        args,
                        printOutput,
                        printErr,
                        self.envVars
                        )
                    runner.start()

                    startedEvent.wait(5)
                    if not startedEvent.isSet():
                        runner.terminate()
                        runner = None
                        tries = tries + 1
                        print "Retrying script ", filename, " as python failed to start."

                if runner is None:
                    print "Test %s failed to start a python process in 5 tries" % filename
                    return False
                else:
                    result = runner.wait(self.getTimeout(script))

                    if result is None:
                        try:
                            runner.terminate()
                        except:
                            print "Failed to terminate test runner: ", traceback.format_exc()
                        print "Test %s timed out" % filename,
                        return False

                    if result != 0:
                        print "Test %s failed" % filename,
                        return False

                return True
        else:
            subprocess.check_call('cd "%s" & c:\python27\python.exe %s '
                % os.path.split(script),
                shell = True
                )

        return True
    
    def mergeTestResultFiles_(self):
        print "Collecting test results."
        scriptDirectories = set([os.path.split(s)[0] for s in self.scripts])
        xunitFiles = [os.path.join(d, f) for d in scriptDirectories for f in os.listdir(d) if f.startswith('nosetests.') and f.endswith('.xml') ]
        index = 1
        for f in xunitFiles:
            try:
                shutil.move(f, self.pathToProjectRoot)
                index += 1
            except IOError:
                pass
        coreFiles = [os.path.join(d, f) for d in scriptDirectories for f in os.listdir(d) if f.startswith('core.') ]
        index = 1
        for f in coreFiles:
            try:
                shutil.move(f, self.pathToProjectRoot)
                index += 1
            except IOError:
                pass

    