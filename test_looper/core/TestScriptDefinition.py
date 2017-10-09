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
                 client_version,
                 periodicTest=False,
                 periodicTestPeriodInHours=defaultPeriodicTestPeriodInHours):
        self.testName = testName
        self.testCommand = testCommand
        self.client_version = client_version

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
            'client_version': self.client_version,
            'periodicTest': self.periodicTest,
            'periodicTestPeriodInHours': self.periodicTestPeriodInHours
            }

    @staticmethod
    def fromJson(json, client_version=None):
        client_version = client_version or \
                         json.get('client_verion')
        
        return TestScriptDefinition(
            json['name'],
            json['command'],
            json.get('machines', {'count': 1, 'cores_min': 0}),
            client_version
            )

    @staticmethod
    def bulk_load(json):
        build_definition = None
        looper_client_version = None
        if isinstance(json, dict) and 'tests' in json:
            build_definition = json.get('build')
            looper_client_version = json.get('test-looper')
            json = json['tests']

        if not isinstance(json, list):
            raise ValueError("Unexpected test definitions file format")

        definitions = [
            TestScriptDefinition.fromJson(row, client_version=looper_client_version)
            for row in json
            ]

        if build_definition:
            build_definition['name'] = 'build'
            definitions.append(
                TestScriptDefinition.fromJson(build_definition,
                                              client_version=looper_client_version)
                )
        elif not [x for x in definitions if x.testName == "build"]:
            definitions.append(
                TestScriptDefinition('build', './make.sh', {'cores': 32}, looper_client_version)
                )

        return definitions



    def __repr__(self):
        return ("TestScriptDefinition(testName=%s, testCommand=%s, machines=%s, "
                "periodicTest=%s,periodicTestPeriodInHours=%s)") % (self.testName,
                                                                    self.testCommand,
                                                                    self.machines,
                                                                    self.periodicTest,
                                                                    self.periodicTestPeriodInHours)

    def isSingleMachineTest(self):
        return self.totalMachinesRequired() == 1

    def totalMachinesRequired(self):
        return self.machines['count']

