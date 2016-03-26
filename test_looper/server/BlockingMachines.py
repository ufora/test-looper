import logging
import time
import uuid
import test_looper.core.TestResult as TestResult

class BlockingMachines(object):
    """BlockingMachines - Models a set of worker machines waiting for test assignments."""
    TIMEOUT = 30

    def __init__(self):
        self.blockingCoreCounts = {}
        self.externalToInternal = {}
        self.testAssignments = {}
        self.lastPingTimes = {}

    def mapCorecountToMachineType(self, coreCount):
        if coreCount <= 4:
            return '2core'
        elif coreCount >= 32:
            return '32core'
        return "%dcore" % coreCount


    def machineCanParticipateInTest(self, testDefinition, workerInfo):
        # GPU instances ONLY run GPU tests
        if testDefinition.gpuTest and workerInfo.instanceType[0] != 'g':
            return False
        if not testDefinition.gpuTest and workerInfo.instanceType[0] == 'g':
            return False

        machineType = self.mapCorecountToMachineType(workerInfo.coreCount)
        return machineType in testDefinition.machineCount

    def resolveInternalAssignments(self, commit, testName):
        testDefinition = commit.getTestDefinitionFor(testName)

        counts = self.blockingMachineTypes()

        if testDefinition.isSatisfiedBy(counts):
            targetCounts = dict(testDefinition.machineCount)

            usedMachines = {}

            for machine,coreCount in self.blockingCoreCounts.iteritems():
                if machine not in self.testAssignments:
                    machineType = self.mapCorecountToMachineType(coreCount)
                    if machineType in targetCounts and targetCounts[machineType] > 0:
                        targetCounts[machineType] -= 1
                        usedMachines[machine] = self.externalToInternal[machine]

            leaderMachine = list(usedMachines)[0]

            newTestResult = TestResult.TestResult.create(
                testName,
                uuid.uuid4().hex,
                commit.commitId,
                leaderMachine,
                usedMachines
                )

            logging.info(
                "Assigning test result %s for definition %s to %s",
                newTestResult,
                testDefinition,
                usedMachines
                )

            for m in usedMachines:
                self.testAssignments[m] = newTestResult

        self.cleanup()

    def cleanup(self):
        toDrop = set()
        for machine, ip in self.externalToInternal.iteritems():
            if time.time() - self.lastPingTimes[machine] > BlockingMachines.TIMEOUT:
                if machine in self.testAssignments:
                    logging.warn(
                        "Machine %s assigned to %s timed out and is not getting its assignment.",
                        machine,
                        self.testAssignments[machine]
                        )
                toDrop.add(machine)

        for machine in toDrop:
            self.remove(machine)

    def remove(self, machine):
        del self.blockingCoreCounts[machine]
        del self.externalToInternal[machine]
        del self.lastPingTimes[machine]

        if machine in self.testAssignments:
            del self.testAssignments[machine]

    def blockingMachineTypes(self):
        blockingMachineTypes = {}

        for machine, coreCount in self.blockingCoreCounts.iteritems():
            if machine not in self.testAssignments:
                typeName = self.mapCorecountToMachineType(coreCount)

                if typeName not in blockingMachineTypes:
                    blockingMachineTypes[typeName] = 0

                blockingMachineTypes[typeName] += 1

        return blockingMachineTypes

    def getTestAssignment(self, commit, testName, workerInfo):
        logging.info(
            "machine %s requesting test assignment for commit %s, test %s, with workerInfo %s",
            workerInfo.machineId,
            commit.commitId,
            testName,
            workerInfo
            )

        self.externalToInternal[workerInfo.machineId] = workerInfo.internalIp
        self.blockingCoreCounts[workerInfo.machineId] = workerInfo.coreCount
        self.lastPingTimes[workerInfo.machineId] = time.time()

        self.resolveInternalAssignments(commit, testName)

        if workerInfo.machineId in self.testAssignments:
            testResult = self.testAssignments[workerInfo.machineId]
            self.remove(workerInfo.machineId)

            logging.info("machine %s assigned test: %s", workerInfo.machineId, testResult)
            return testResult

        logging.info("machine %s is now blocked.", workerInfo.machineId)

        return None
