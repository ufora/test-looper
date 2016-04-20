import logging
import datetime
import dateutil.parser

class TestLooperEc2Machines(object):
    """This is a thin wrapper around a TestLooperEc2Connection to provide
    utility functionaltiy for checking and modifying spot requests and spot instances"""
    def __init__(self, ec2Connection):
        self.ec2 = ec2Connection

    def provisionedMachineCount(self, instanceType):
        if instanceType == None:
            assert False
        instances = self.ec2.getLooperInstances()
        filtered = [
            instance for instance in instances
            if instance.instance_type == instanceType and \
                (instance.state == 'running' or instance.state == 'pending')
            ]

        return len(filtered)

    def isMachineAlive(self, machineId):
        return self.ec2.isMachineAlive(machineId)

    def getCurrentAvailabilityZone(self):
        # count the number of distinct availability zones
        spotRequests = self.ec2.getAllSpotRequestObjects()
        openRequests = [req for req in spotRequests if req.state == 'active']
        azCount = {}
        mostPopulatedAZ = None
        mostPopulatedAZCount = 0
        for req in openRequests:
            az = req.launched_availability_zone
            if az is None:
                continue
            if not az in azCount:
                azCount[az] = 0

            azCount[az] += 1
            newCount = azCount[az]
            if newCount > mostPopulatedAZCount:
                mostPopulatedAZCount = newCount
                mostPopulatedAZ = az

        if len(azCount) == 0:
            logging.info("No active test looper spot requests in any availability zone")
            return "us-west-2a"
        if len(azCount) > 1:
            logging.warn("WARNING: Machines are launched in more than one availability zone!")
        return mostPopulatedAZ

    def cancelSpotRequestsInLaunchGroup(self, launchGroup):
        requests = self.ec2.getAllSpotRequestObjects()

        filtered = {req for req in requests if req.launch_group == launchGroup}
        ids = [req.id for req in filtered]
        if len(ids) > 0:
            self.ec2.cancelSpotRequests(ids)

    def cancelOpenSpotRequestsNotInLaunchGroup(self,
                                               instanceType,
                                               requestCount,
                                               launchGroupToIgnore,
                                               minBid=0):
        requests = self.ec2.getAllSpotRequestObjects()

        filtered = {
            req for req in requests
            if req.price >= minBid
            and req.instance_id is None
            and req.launch_specification.instance_type == instanceType
            and req.state == 'open'
            and req.launch_group != launchGroupToIgnore
            }
        ids = [req.id for req in filtered]
        toTake = min(requestCount, len(ids))
        toCancel = ids[:toTake]
        if len(toCancel) > 0:
            self.ec2.cancelSpotRequests(toCancel)
        return toTake

    def spotRequestCount_(self, requestFilter):
        requests = self.ec2.getAllSpotRequestObjects()

        filteredOnPrice = [req for req in requests if requestFilter(req)]
        return len(filteredOnPrice)

    def spotRequestsInLaunchGroup(self, instanceType, launchGroup):
        def requestFilter(spotRequest):
            return spotRequest.launch_group == launchGroup and \
                spotRequest.launch_specification.instance_type == instanceType and \
                spotRequest.state != 'cancelled'

        return self.spotRequestCount_(requestFilter)

    def spotRequestsNotInLaunchGroup(self, instanceType, launchGroupToIgnore, minBid):
        def requestFilter(spotRequest):
            return spotRequest.launch_group != launchGroupToIgnore and \
                spotRequest.price >= minBid and \
                spotRequest.launch_specification.instance_type == instanceType and \
                spotRequest.state != 'cancelled'

        return self.spotRequestCount_(requestFilter)

    def getNumberOfCurrentlyProvisionedMachines(self, instanceType):
        instances = self.ec2.getLooperInstances()
        return len([inst for inst in instances if inst.instance_type == instanceType])


    def provisionSpotInstancesByLaunchGroup(self,
                                            instanceType,
                                            machineCount,
                                            launchGroup,
                                            bid,
                                            availabilityZone):
        logging.info("Provisioning spot instances by launch group: %s", launchGroup)
        self.ec2.requestLooperInstances(
            bid,
            instanceType,
            machineCount,
            launchGroup,
            availabilityZone
            )

    @staticmethod
    def convertDateTimeToSecondsFromEpoch(dt):
        epoch = datetime.datetime.utcfromtimestamp(0)
        delta = dt - epoch
        return delta.total_seconds()

    def isInstanceAboutToBeBilled(self, instance, minutesBeforeBillingPeriodToCancel):
        #Notice: current time is in the local time zone, instance launch time is in UTC
        #I'm not worrying about converting timezones, because we only care about the
        #time difference with respect to the minutes elapsed since the last time we were
        #billed
        currentTime = datetime.datetime.now()
        instanceLaunchTime = dateutil.parser.parse(instance.launch_time).replace(tzinfo=None)

        s1 = self.convertDateTimeToSecondsFromEpoch(currentTime)
        s2 = self.convertDateTimeToSecondsFromEpoch(instanceLaunchTime)

        diff = s1 - s2

        lifetimeInMinutes = diff / 60.0
        minutesToNextBill = 60 - (lifetimeInMinutes % 60)
        logging.warn("Minutes to next bill: %s, target: %s",
                     minutesToNextBill,
                     minutesBeforeBillingPeriodToCancel)
        return minutesToNextBill < minutesBeforeBillingPeriodToCancel

    def getIdsForInstancesAboutToBeBilled(self, instanceType, minutesBeforeBillingPeriodToCancel):
        instances = self.ec2.getLooperInstances()
        instanceIds = []
        logging.info("Expiring Instances. Machine type: %s, instance count: %s",
                     instanceType,
                     len(instances))
        for instance in instances:
            if instance.instance_type != instanceType:
                continue
            if self.isInstanceAboutToBeBilled(instance, minutesBeforeBillingPeriodToCancel):
                spotInstanceID = instance.spot_instance_request_id
                instanceIds.append(spotInstanceID)

        return instanceIds

    def cancelSpotRequests(self, ids):
        self.ec2.cancelSpotRequests(ids)

    def cancelSpotInstancesAboutToBeBilledInLaunchGroup(self,
                                                        launchGroup,
                                                        minutesBeforeBillingPeriodToCancel):
        logging.info("Canceling launch group request for group: %s", launchGroup)
        spotRequstObjectsInLaunchGroup = [
            req for req in self.ec2.getAllSpotRequestObjects()
            if req.launch_group == launchGroup
            ]
        # I don't think there is a filter for just getting instances within
        # a particular launch group:
        # http://docs.aws.amazon.com/AWSEC2/latest/CommandLineReference/ApiReference-cmd-DescribeInstances.html
        spotRequestIds = [req.id for req in spotRequstObjectsInLaunchGroup]
        allInstances = self.ec2.getLooperInstances()
        matchedInstances = [
            inst for inst in allInstances
            if inst.spot_instance_request_id in spotRequestIds
            ]

        openRequestIds = [req.id for req in spotRequstObjectsInLaunchGroup if req.state == 'open']
        logging.info("Open spot requests in %s launchGroup to cancel: %s",
                     launchGroup,
                     openRequestIds)
        logging.info("Canceling open requests: %s", openRequestIds)
        # We're canceling all open spot requests at this point,
        # because we're about to reprovision our desired set of spot instances
        self.ec2.cancelSpotRequests(openRequestIds)

        idsToCancel = [
            inst.spot_instance_request_id for inst in matchedInstances
            if self.isInstanceAboutToBeBilled(inst, minutesBeforeBillingPeriodToCancel)
            ]
        logging.info("Expiring spot requests in %s launchGroup to cancel: %s",
                     launchGroup,
                     idsToCancel)
        self.ec2.cancelSpotRequests(idsToCancel)

    def requestSpotInstance(self, instanceType, bid, availabilityZone):
        self.ec2.requestLooperInstances(
            bid,
            instance_type=instanceType,
            availability_zone=availabilityZone
            )
