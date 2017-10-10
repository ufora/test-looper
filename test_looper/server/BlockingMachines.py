import logging
import time
import uuid
import test_looper.core.TestResult as TestResult

class BlockingMachines(object):
    """BlockingMachines - Models a set of worker machines waiting for test assignments."""
    TIMEOUT = 30

    def __init__(self):
        self.machines = {}
        self.testAssignments = {}
        self.lastPingTimes = {}


    @staticmethod
    def machineCanParticipateInTest(workerInfo, testDefinition):
        machines = testDefinition.machines

        # GPU instances ONLY run GPU tests
        if machines.get('gpu', False) and not workerInfo.isGpuInstance():
            return False
        if not machines.get('gpu', False) and workerInfo.isGpuInstance():
            return False

        if 'cores' in machines and machines['cores'] == workerInfo.coreCount:
            return True

        if 'cores_min' in machines and machines['cores_min'] <= workerInfo.coreCount:
            return True

        return False


    def resolveInternalAssignments(self, commit, testName):
        testDefinition = commit.getTestDefinitionFor(testName)
        assignedMachines = []

        for workerInfo in self.machines.itervalues():
            if workerInfo.machineId in self.testAssignments:
                continue

            if self.machineCanParticipateInTest(workerInfo, testDefinition):
                assignedMachines.append(workerInfo)
                if len(assignedMachines) == testDefinition.machines['count']:
                    break

        if len(assignedMachines) == testDefinition.machines['count']:
            leaderMachine = sorted(assignedMachines,
                                   key=lambda m: m.internalIpAddress)[0]

            newTestResult = TestResult.TestResult.create(
                testName,
                uuid.uuid4().hex,
                commit.commitId,
                leaderMachine.machineId,
                {m.machineId: m.internalIpAddress for m in assignedMachines}
                )

            logging.info(
                "Assigning test result %s for definition %s to %s",
                newTestResult,
                testDefinition,
                assignedMachines
                )

            for m in assignedMachines:
                self.testAssignments[m.machineId] = newTestResult


        self.cleanup()


    def cleanup(self):
        toDrop = set()
        for workerInfo in self.machines.itervalues():
            if time.time() - self.lastPingTimes[workerInfo.machineId] > BlockingMachines.TIMEOUT:
                if workerInfo.machineId in self.testAssignments:
                    logging.warn(
                        "Machine %s assigned to %s timed out and is not getting its assignment.",
                        workerInfo,
                        self.testAssignments[workerInfo.machineId]
                        )
                toDrop.add(workerInfo.machineId)

        for machineId in toDrop:
            self.remove(machineId)

    def remove(self, machineId):
        del self.machines[machineId]
        del self.lastPingTimes[machineId]

        if machineId in self.testAssignments:
            del self.testAssignments[machineId]


    def getTestAssignment(self, commit, testName, workerInfo):
        logging.info(
            "machine %s requesting test assignment for commit %s, test %s, with workerInfo %s",
            workerInfo.machineId,
            commit.commitId,
            testName,
            workerInfo
            )

        self.machines[workerInfo.machineId] = workerInfo
        self.lastPingTimes[workerInfo.machineId] = time.time()

        self.resolveInternalAssignments(commit, testName)

        if workerInfo.machineId in self.testAssignments:
            testResult = self.testAssignments[workerInfo.machineId]
            self.remove(workerInfo.machineId)

            logging.info("machine %s assigned test: %s", workerInfo.machineId, testResult)
            return testResult

        logging.info("machine %s is now blocked.", workerInfo.machineId)

        return None
