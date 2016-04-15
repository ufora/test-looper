import logging
import time
import threading
import traceback

class TestLooperAutoProvisioner(object):
    def __init__(self,
                 testManager,
                 ec2Interface,
                 billingPeriodInMinutes=60,
                 minutesBeforeExpirationToDeprovisionMachine=4):
        self.ec2Interface = ec2Interface
        self.testManager = testManager
        self.billingPeriodInMinutes = billingPeriodInMinutes
        self.minutesBeforeExpirationToDeprovisionMachine = \
            minutesBeforeExpirationToDeprovisionMachine

        machineInfo = {
            'c3.xlarge' : {
                'bid': .3,
                'coreCount': 4
                },
            'c3.8xlarge' : {
                'bid': .9,
                'coreCount': 32
                },
            'c4.xlarge' : {
                'bid': .3,
                'coreCount': 4
                },
            'c4.8xlarge' : {
                'bid': .9,
                'coreCount': 36
                },
            'g2.2xlarge' : {
                'bid': .3,
                'coreCount': 8
                },
            'g2.8xlarge' : {
                'bid': .9,
                'coreCount': 32
                }
        }

        self.testScriptDefinitionsMachineTypeToInstanceType = {
            '2core' :  'c3.xlarge',
            '32core' : 'c3.8xlarge'
        }

        self.machineTypeToBid = {m: machineInfo[m]['bid'] for m in machineInfo}

        self.availableInstancesAndCoreCount = [
            (m, machineInfo[m]['coreCount']) for m in machineInfo
            ]

        self.periodicTestLaunchGroup = "periodic_test_launch_group"


        self.availabilityZone = ec2Interface.getCurrentAvailabilityZone()
        logging.warn("Resolved availabilityZone: %s", self.availabilityZone)

        self.running = False
        self.tornDown = False
        self.provisionWorkersPeriod = 20.0
        self.topCommitsToTakeForPeriodicTests = 1

        self.lastProvisionAttempt = 0
        self.toProvisionAsSingleLaunchGroup = {}
        self.workerProvisionerThread = None
        self.cancelAutoScaleThreadEvent = threading.Event()
        self.start()

    def start(self):
        self.workerProvisionerThread = threading.Thread(target=self._autoProvisionLoop)
        self.workerProvisionerThread.start()
        logging.info("Started test looper machines")

    def isMachineAlive(self, machineId):
        return self.ec2Interface.isMachineAlive(machineId)

    def calculateTargetMachineCounts(self, periodicTests, numberOfTestsToProvisionFor):
        """
        Returns a dictionary mapping machine type to the number of instances of
        that machine we want to provision
        """

        with self.testManager.lock:
            candidates = self.testManager.prioritizeCommitsAndTests(periodicTests)

        positivePriorityValues = [i for i in candidates if i.priority > 0]

        toTake = min(numberOfTestsToProvisionFor, len(candidates))
        topCommits = positivePriorityValues[:toTake]
        machineTypeToMachineCount = {}

        for candidate in topCommits:
            machineCounts = candidate.machineCount()
            for machineCores, numMachines in machineCounts.iteritems():
                instanceType = self.testScriptDefinitionsMachineTypeToInstanceType[machineCores]

                if instanceType in machineTypeToMachineCount:
                    machineTypeToMachineCount[instanceType] += numMachines
                else:
                    machineTypeToMachineCount[instanceType] = numMachines

        for trackedInstanceType in self.machineTypeToBid.keys():
            if trackedInstanceType not in machineTypeToMachineCount:
                machineTypeToMachineCount[trackedInstanceType] = 0

        assert len(machineTypeToMachineCount) == len(self.machineTypeToBid.keys())
        return machineTypeToMachineCount

    def isAutoProvisionerEnabled(self):
        return self.running

    def toggleAutoProvisioner(self):
        self.running = not self.running
        return self.running

    def getSpotRequestsInLaunchGroup(self, instanceType, launchGroup):
        if instanceType is None:
            toReturn = {}
            for trackedType in self.machineTypeToBid.keys():
                toReturn[trackedType] = self.ec2Interface.spotRequestsInLaunchGroup(
                    trackedType,
                    launchGroup
                    )
            return toReturn

        return self.ec2Interface.spotRequestsInLaunchGroup(
            instanceType,
            launchGroup
            )

    def updateSpotRequestsToMatchOurTargets(self, toProvisionAsSingleLaunchGroup):
        for machineTypeToProvision in toProvisionAsSingleLaunchGroup:
            targetLaunchGroup = self.periodicTestLaunchGroup + "_" + machineTypeToProvision
            currentCount = self.getSpotRequestsInLaunchGroup(
                instanceType=machineTypeToProvision,
                launchGroup=targetLaunchGroup
                )
            targetCount = toProvisionAsSingleLaunchGroup[machineTypeToProvision]
            diff = currentCount - targetCount
            logging.info("Single launch group - Machine type: %s, %s, %s, diff: %s",
                         machineTypeToProvision,
                         targetCount,
                         currentCount,
                         diff)
            if diff == 0 or time.time() - self.lastProvisionAttempt < 60:
                continue
            bid = self.machineTypeToBid[machineTypeToProvision]
            self.ec2Interface.cancelSpotInstancesAboutToBeBilledInLaunchGroup(
                targetLaunchGroup,
                self.minutesBeforeExpirationToDeprovisionMachine
                )
            if currentCount > 0:
                logging.warn(
                    "%s machines, launch group: %s, target: %s, waiting for these to be canceled before provisioning a new set",
                    currentCount,
                    targetLaunchGroup,
                    targetCount)
            elif currentCount == 0:
                self.ec2Interface.provisionSpotInstancesByLaunchGroup(
                    machineTypeToProvision,
                    targetCount,
                    targetLaunchGroup,
                    bid,
                    self.availabilityZone
                    )
                self.lastProvisionAttempt = time.time()

    def getNumberOfCurrentlyProvisionedMachines(self, instanceType):
        return self.ec2Interface.getNumberOfCurrentlyProvisionedMachines(instanceType)

    def _autoProvisionLoop(self):
        """Periodically calculate how many machines to provision as a single launch group
        and check that our issued spot requests matches our targets"""
        logging.info("Entering provision workers loop")
        while not self.tornDown:
            if self.running:
                with self.testManager.lock:
                    periodicTests = self.testManager.getPeriodicTestsToRun()
                self.toProvisionAsSingleLaunchGroup = \
                    self.calculateTargetMachineCounts(periodicTests,
                                                      self.topCommitsToTakeForPeriodicTests)
                self.updateSpotRequestsToMatchOurTargets(self.toProvisionAsSingleLaunchGroup)
                logging.info("Updated spot requests")

            self.cancelAutoScaleThreadEvent.wait(self.provisionWorkersPeriod)

        logging.info("Exited provision workers loop")

    def updateAvailabilityZone(self, az):
        self.availabilityZone = az
        return az

    def getAutoProvisionerState(self):
        state = {}
        try:
            state["running"] = self.running
            state["topCommitsToTakeForPeriodicTests"] = self.topCommitsToTakeForPeriodicTests
            state["allTrackedMachineTypes"] = self.machineTypeToBid.keys()
            state["requestBid"] = self.machineTypeToBid
            state["targetMachinesAsSingleLaunchGroup"] = self.toProvisionAsSingleLaunchGroup
            state["availabilityZone"] = self.availabilityZone
            return state
        except Exception as e:
            logging.error("Failed to get auto provisioner state, %s", traceback.format_exc())
            return state

    def stop(self):
        try:
            self.running = False
            self.tornDown = True
            self.cancelAutoScaleThreadEvent.set()
            self.workerProvisionerThread.join(5.0)
            if self.workerProvisionerThread.isAlive():
                logging.error("Failed to join worker provisioner thread")
        except:
            logging.warn("Worker provisioner thread was not started")

