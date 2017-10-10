import test_looper.core.cloud.MachineInfo as MachineInfo

class NoCloud:
    def __init__(self):
        pass

    def getOwnMachineInfo(self):
        return MachineInfo.MachineInfo(
            "localhost",
            "localhost",
            1,
            None,
            None
            )

    def tagInstance(self, machineId):
        pass

    def isMachineAlive(self, machineId):
        return True

    def isSpotEnabled(self):
        return False