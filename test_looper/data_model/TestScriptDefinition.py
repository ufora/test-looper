"""
TestScriptDefinition

Models a single unit-test script and the resources required to execute it.
"""

import logging

class TestDependency(object):
    """A block of well-specified data exposed as a dependency to a test or build."""
    def __init__(self, exposedAs):
        """exposedAs - a string identifying the name by which this dependency is exposed."""
        self.exposedAs = exposedAs

    @staticmethod
    def fromJson(json):
        whichCls = json['type']
        if whichCls == "build":
            return BuildTestDependency.fromJson(json)
        if whichCls == "source":
            return SourceTestDependency.fromJson(json)
        if whichCls == "data":
            return RawDataDependency.fromJson(json)

        raise Exception("Unknown dependency type: " + str(whichCls))


class BuildTestDependency(TestDependency):
    """A build artifact from another test-looper test."""
    def __init__(self, exposedAs, commitId, testName):
        """exposedAs - a string identifying the name by which this dependency is exposed.
        commitId - a repo/commitId identifying the specific source for this. If None, then the current commit.
        testName - the name of the test to depend on
        """
        TestDependency.__init__(self, exposedAs)

        self.commitId = commitId
        self.testName = testName

    @staticmethod
    def fromJson(json):
        return BuildTestDependency(
            json['exposedAs'],
            json['commitId'],
            json['testName']
            )

    def toJson(self):
        return {
            'type': 'build',
            'exposedAs': self.exposedAs,
            'commitId': self.commitId,
            'testName': self.testName
            }

class SourceTestDependency(TestDependency):
    """A the source-code from some other repo."""
    def __init__(self, exposedAs, commitId):
        """exposedAs - a string identifying the name by which this dependency is exposed.
        commitId - a repo/commitId identifying the specific source for this.
        """
        TestDependency.__init__(self, exposedAs)

        self.commitId = commitId

    @staticmethod
    def fromJson(json):
        return SourceTestDependency(
            json['exposedAs'],
            json['commitId']
            )

    def toJson(self):
        return {
            'type': 'source',
            'exposedAs': self.exposedAs,
            'commitId': self.commitId
            }

        
class RawDataDependency(TestDependency):
    """A zipped directory of data indexed by tarball.

    The main test-looper infrastructure should be configured
    with a set of locations it can look for these (e.g. S3 buckets,
    file servers). Because the SHA hash identifies the data uniquely,
    we don't need to specify the source location here.
    """
    def __init__(self, exposedAs, shaHash):
        TestDependency.__init__(self, exposedAs)
        
        self.shaHash = shaHash

    @staticmethod
    def fromJson(json):
        return RawDataDependency(
            json['exposedAs'],
            json['shaHash']
            )

    def toJson(self):
        return {
            'type': 'data',
            'exposedAs': self.exposedAs,
            'shaHash': self.shaHash
            }

        
class TestDefinition(object):
    def __init__(self,
                 testName,
                 testType,
                 testCommand,
                 docker,
                 portExpose,
                 dependencies
                 ):
        """Initialize a single Test type.

        testName - string giving the name of the test
        testType - one of:
            "build" - a script that produces a test artifact. This isn't supposed to ever fail, so if it
                does, we assume it's a broken build.
            "test" - a test of functionality. We may run this multiple times to verify a failure rate.
            "environment" - a test environment that we can spin up to interrogate. May expose
                some subset of services on various ports
        docker - a "Docker" instance, describing the docker environment in which we should run the test
        testCommand - the command to run that executes the test.
        portExpose - None, or a dictionary of service names and ports.
        dependencies - a list of test dependencies we depend on
        """
        self.testName = testName
        self.testCommand = testCommand
        self.testType = testType
        self.docker = docker
        self.portExpose = portExpose
        self.dependencies = dependencies


    def toJson(self):
        return {
            'name': self.testName,
            'command': self.testCommand,
            'machines': self.machines,
            'docker': self.docker,
            'portExpose': self.portExpose,
            'dependencies': [d.toJson() for d in self.dependencies]
            }

    @staticmethod
    def fromJson(json, docker=None):
        #allow individual tests to override their image configuration
        return TestDefinition(
            json['name'],
            json['type'],
            json['command'],
            docker or json['docker'],
            json.get("portExpose"),
            [TestDependency.fromJson(d) for d in json['dependencies']] if 'dependencies' in json else []
            )

    def __repr__(self):
        return "TestDefinition(%s)" % self.toJson()

class TestDefinitions:
    def __init__(self, definitions):
        """definitions - a dict from testName to the test definition"""
        self.definitions = definitions

    @staticmethod
    def fromJson(json):
        if json.get("looper_version", 0) < 2:
            raise ValueError("TestDefinitions file is for an earlier version of test looper")

        if json.get("looper_version") == 2:
            docker_base = json.get("docker")

        if not isinstance(json, dict):
            raise ValueError("testDefinitions.json should be an object")

        for m in ['build', 'tests', 'docker']:
            if not m in json:
                raise ValueError("testDefinitions.json should have a member " + m)

        build_definition = json.get('build')
        docker = json.get('docker')
        
        tests = [
            TestScriptDefinition.fromJson(row, docker=docker)
            for row in json['tests']
            ] if 'tests' in json else []

        environments = [
            TestScriptDefinition.fromJson(row, docker=docker)
            for row in json['environments']
            ] if 'environments' in json else []

        build_definition['name'] = 'build'
        build_definition = TestScriptDefinition.fromJson(build_definition, docker=docker)

        return TestDefinitions(
            docker, 
            build_definition, 
            {t.testName: t for t in tests}, 
            {t.testName: t for t in environments}
            )

