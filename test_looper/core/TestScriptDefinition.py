"""
TestScriptDefinition

Models a single unit-test script and the resources required to execute it.
"""

import logging

class TestScriptDefinition:
    validMachineDescriptions = set(["any", "2core", "8core", "32core"])

    defaultPeriodicTestPeriodInHours = 12

    def __init__(self,
                 testName,
                 testScriptPath,
                 machineCount,
                 periodicTest=False,
                 gpuTest=False,
                 periodicTestPeriodInHours=defaultPeriodicTestPeriodInHours):
        self.testName = testName
        self.testScriptPath = testScriptPath

        for m in machineCount:
            assert isinstance(machineCount[m], int)
            assert machineCount[m] > 0
            assert machineCount[m] < 100
            assert m in TestScriptDefinition.validMachineDescriptions

        self.machineCount = machineCount
        self.periodicTest = periodicTest
        self.gpuTest = gpuTest

        if isinstance(periodicTestPeriodInHours, str):
            periodicTestPeriodInHours = float(periodicTestPeriodInHours)
            logging.warn("casted %s to a float", periodicTestPeriodInHours)

        self.periodicTestPeriodInHours = periodicTestPeriodInHours

    def toJson(self):
        return {
            'testName': self.testName,
            'testScriptPath': self.testScriptPath,
            'machineCount': self.machineCount,
            'periodicTest': self.periodicTest,
            'gpuTest': self.gpuTest,
            'periodicTestPeriodInHours': self.periodicTestPeriodInHours
            }

    @staticmethod
    def fromJson(json):
        return TestScriptDefinition(
            json['testName'],
            json['testScriptPath'],
            json['machineCount'],
            json['periodicTest'] if 'periodicTest' in json else False,
            json['gpuTest'] if 'gpuTest' in json else False,
            json['periodicTestPeriodInHours'] if 'periodicTestPeriodInHours' in json \
                else TestScriptDefinition.defaultPeriodicTestPeriodInHours
            )

    def __repr__(self):
        return "TestScriptDefinition(testName=%s,testScriptPath=%s,machineCount=%s,periodicTest=%s,gpuTest=%s,periodicTestPeriodInHours=%s)" % (
            self.testName,
            self.testScriptPath,
            self.machineCount,
            self.periodicTest,
            self.gpuTest,
            self.periodicTestPeriodInHours
            )

    def isSingleMachineTest(self):
        return self.totalMachinesRequired() == 1

    def totalMachinesRequired(self):
        return sum(self.machineCount.values())

    def isSatisfiedBy(self, machineCount):
        for m in self.machineCount:
            if m not in machineCount or machineCount[m] < self.machineCount[m]:
                logging.info("Test definition not satisfied by available machines.\n" + \
                             "Test definition: %s\Available machines: %s",
                             self.machineCount, machineCount)
                return False
        return True
