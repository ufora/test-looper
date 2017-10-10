
class MachineInfo:
    def __init__(self, machineId, internalIpAddress, coreCount, availabilityZone, instanceType):
        self.machineId = machineId
        self.internalIpAddress = internalIpAddress
        self.coreCount = coreCount
        self.availabilityZone = availabilityZone
        self.instanceType = instanceType

    def isGpuInstance(self):
        if self.instanceType is None:
            return False

        return self.instanceType[0] == "g"
        
    def toJson(self):
        return dict(self.__dict__)

    @staticmethod
    def fromJson(json):
        return MachineInfo(**json)