"""
TestScriptDefinition

Models a single unit-test script and the resources required to execute it.
"""

import logging

class TestScriptDefinition(object):
    defaultPeriodicTestPeriodInHours = 12

    def __init__(self,
                 testName,
                 testScriptPath,
                 machines,
                 client_version,
                 periodicTest=False,
                 periodicTestPeriodInHours=defaultPeriodicTestPeriodInHours):
        self.testName = testName
        self.testScriptPath = testScriptPath
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
            'command': self.testScriptPath,
            'machines': self.machines,
            'client_version': self.client_version,
            'periodicTest': self.periodicTest,
            'periodicTestPeriodInHours': self.periodicTestPeriodInHours
            }

    @staticmethod
    def fromJson(json, client_version=None):
        client_version = client_version or \
                         json.get('client_verion')
        if 'testName' in json:
            return TestScriptDefinition.fromJson_old(json, client_version)

        return TestScriptDefinition(
            json['name'],
            json['command'],
            json['machines'],
            client_version
            )

    ###########
    # Backward compatibility for old testDefinitions.json format
    # This entire section can eventually be removed
    validMachineDescriptions = set(["2core", "8core", "32core"])
    @staticmethod
    def old_machineCount_to_machines(machineCount):
        assert len(machineCount) == 1
        machine, count = machineCount.iteritems().next()
        assert machine in TestScriptDefinition.validMachineDescriptions
        if machine == "2core":
            cores = 4
        elif machine == "8core":
            cores = 8
        elif machine == "32core":
            cores = 32
        return {"cores": cores, "count": count}

    @staticmethod
    def fromJson_old(json, client_version):
        machines = TestScriptDefinition.old_machineCount_to_machines(json['machineCount'])
        if 'gpuTest' in json:
            machines['gpu'] = True

        return TestScriptDefinition(
            json['testName'],
            "./make.sh test %s" % json['testScriptPath'],
            machines,
            client_version,
            json['periodicTest'] if 'periodicTest' in json else False,
            json['periodicTestPeriodInHours'] if 'periodicTestPeriodInHours' in json \
                else TestScriptDefinition.defaultPeriodicTestPeriodInHours
            )
    # End of back-compat section
    ########

    def __repr__(self):
        return ("TestScriptDefinition(testName=%s, testScriptPath=%s, machines=%s, "
                "periodicTest=%s,periodicTestPeriodInHours=%s)") % (self.testName,
                                                                    self.testScriptPath,
                                                                    self.machines,
                                                                    self.periodicTest,
                                                                    self.periodicTestPeriodInHours)

    def isSingleMachineTest(self):
        return self.totalMachinesRequired() == 1

    def totalMachinesRequired(self):
        return self.machines['count']

