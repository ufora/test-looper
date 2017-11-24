"""
TestScriptDefinition

Models a single unit-test script and the resources required to execute it.
"""

import logging

class TestScriptDefinition(object):
    defaultPeriodicTestPeriodInHours = 12

    def __init__(self,
                 testName,
                 testCommand,
                 machines,
                 docker,
                 periodicTest=False,
                 periodicTestPeriodInHours=defaultPeriodicTestPeriodInHours,
                 portExpose=None
                 ):
        self.testName = testName
        self.testCommand = testCommand
        self.docker = docker
        self.portExpose = portExpose

        if 'count' not in machines:
            machines['count'] = 1

        assert isinstance(machines['count'], int)
        assert machines['count'] > 0
        assert machines['count'] < 100

        self.machines = machines
        self.periodicTest = periodicTest

        if isinstance(periodicTestPeriodInHours, str):
            periodicTestPeriodInHours = float(periodicTestPeriodInHours)
            logging.warn("casted %s to a float", periodicTestPeriodInHours)

        self.periodicTestPeriodInHours = periodicTestPeriodInHours

    def toJson(self):
        return {
            'name': self.testName,
            'command': self.testCommand,
            'machines': self.machines,
            'periodicTest': self.periodicTest,
            'periodicTestPeriodInHours': self.periodicTestPeriodInHours,
            'docker': self.docker,
            'portExpose': self.portExpose
            }

    @staticmethod
    def fromJson(json, docker=None):
        #allow individual tests to override their image configuration
        if "docker" in json:
            docker = json["docker"]

        return TestScriptDefinition(
            json['name'],
            json['command'],
            json.get('machines', {'count': 1, 'cores_min': 0}),
            docker,
            portExpose=json.get("portExpose")
            )

    @staticmethod
    def testSetFromJson(json):
        return TestDefinitions.fromJson(json).getTestsAndBuild()


    def __repr__(self):
        return ("TestScriptDefinition(testName=%s, testCommand=%s, machines=%s, "
                "periodicTest=%s,periodicTestPeriodInHours=%s,ports=%s)") % (self.testName,
                                                                    self.testCommand,
                                                                    self.machines,
                                                                    self.periodicTest,
                                                                    self.periodicTestPeriodInHours,
                                                                    self.portExpose
                                                                    )

    def isSingleMachineTest(self):
        return self.totalMachinesRequired() == 1

    def totalMachinesRequired(self):
        return self.machines['count']

class TestDefinitions:
    def __init__(self, docker, build, tests, environments):
        self.docker = docker
        self.build = build
        self.tests = tests
        self.environments = environments

    def getTestsAndBuild(self):
        res = [self.build] + list(self.tests.values())
        return res

    def all(self):
        return {k.testName: k for k in [self.build] + list(self.tests.values()) + list(self.environments.values())}

    @staticmethod
    def fromJson(json):
        build_definition = None
        
        if json.get("looper_version", 0) < 1:
            raise ValueError("TestDefinitions file is for an earlier version of test looper")

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

