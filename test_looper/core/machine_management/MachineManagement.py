import test_looper.core.algebraic as algebraic
import test_looper.worker.TestLooperWorker as TestLooperWorker
import test_looper.worker.WorkerState as WorkerState
import test_looper.core.machine_management.AwsCloudAPI as AwsCloudAPI
import uuid
import docker
import logging
import threading
import traceback
import os

HardwareConfig = algebraic.Alternative("HardwareConfig")
HardwareConfig.Config = {
    "cores": int,
    "ram_gb": int
    }

OsConfiguration = algebraic.Alternative("OsConfiguration")
OsConfiguration.LinuxWithDocker = {}
OsConfiguration.WindowsWithDocker = {}
OsConfiguration.WindowsOneshot = {"ami": str}
OsConfiguration.LinuxOneshot = {"ami": str}


class MachineManagement(object):
    """Base class for 'machine management' which is responsible for booting workers to work on tests."""
    def __init__(self, config, serverPortConfig, source_control, artifactStorage):
        self.source_control = source_control
        self.serverPortConfig = serverPortConfig
        self.artifactStorage = artifactStorage
        self.config = config

        self.hardwareConfigs = {}
        self.osConfigs = {}
        self.runningMachines = {}

        self.ram_gb_booted = 0
        self.cores_booted = 0
        self._lock = threading.RLock()

    def canBoot(self, hardwareConfig, osConfig):
        with self._lock:
            if not (self.ram_gb_booted + hardwareConfig.ram_gb <= self.config.max_ram_gb or self.config.max_ram_gb <= 0):
                return False

            if not (self.cores_booted + hardwareConfig.cores <= self.config.max_cores or self.config.max_cores <= 0):
                return False

            if not (len(self.runningMachines) + 1 <= self.config.max_workers or self.config.max_workers <= 0):
                return False

            return True

    def _machineBooted(self, machineId, hardwareConfig, osConfig, machine):
        with self._lock:
            assert self.canBoot(hardwareConfig, osConfig)

            self.hardwareConfigs[machineId] = hardwareConfig
            self.osConfigs[machineId] = osConfig
            self.runningMachines[machineId] = machine

            self.ram_gb_booted += hardwareConfig.ram_gb
            self.cores_booted += hardwareConfig.cores

    def _machineRemoved(self, machineId):
        with self._lock:
            hardwareConfig = self.hardwareConfigs[machineId]

            del self.hardwareConfigs[machineId]
            del self.osConfigs[machineId]
            del self.runningMachines[machineId]

            self.ram_gb_booted -= hardwareConfig.ram_gb
            self.cores_booted -= hardwareConfig.cores

    def worker_alive(self, machineId):
        with self._lock:
            return machineId in self.runningMachines


    def all_hardware_configs(self):
        """Return a list of HardwareConfig objects that we could boot in order of preference"""
        assert False, "Subclasses implement"

    def synchronize_workers(self, machineIds):
        """Ensure that no workers not in 'machineIds' are up.

        returns a list of machineIds that appear dead."""
        assert False, "Subclasses implement"

    def terminate_worker(self, machineId):
        """Ensure a worker identified by 'machineId' is terminated"""
        assert False, "Subclsses implement"

    def boot_worker(self, hardware_config, os_config):
        """Boot a worker in a given configuration and return a unique machineId (string) 

        TestId is passed to the worker so it can request specific tests if necessary.

        return None if not possible to boot such a worker.
        """
        assert False, "Subclsses implement"


class DummyMachineManagement(MachineManagement):
    def __init__(self, config, serverPortConfig, source_control, artifactStorage):
        MachineManagement.__init__(self, config, serverPortConfig, source_control, artifactStorage)

    def all_hardware_configs(self):
        return [
            HardwareConfig.Config(cores=1, ram_gb=4),
            HardwareConfig.Config(cores=4, ram_gb=16)
            ]

    def synchronize_workers(self, machineIds):
        with self._lock:
            for machineId in list(self.runningMachines):
                if machineId not in machineIds:
                    self._machineRemoved(machineId)

            return [machineId for machineId in machineIds if machineId not in self.runningMachines]

    def terminate_worker(self, machineId):
        with self._lock:
            if machineId in self.runningMachines:
                self._machineRemoved(machineId)
            else:
                assert False, "Don't know about machine %s" % machineId

    def boot_worker(self, hardware_config, os_config):
        with self._lock:
            if hardware_config not in self.all_hardware_configs():
                return None

            machineId = "worker_" + str(uuid.uuid4()).replace("-","")[:10]

            self._machineBooted(machineId, hardware_config, os_config, True)

            return machineId

class LocalMachineManagement(MachineManagement):
    def __init__(self, config, serverPortConfig, source_control, artifactStorage):
        MachineManagement.__init__(self, config, serverPortConfig, source_control, artifactStorage)

    def all_hardware_configs(self):
        return [
            HardwareConfig.Config(cores=1, ram_gb=4)
            ]

    def synchronize_workers(self, machineIds):
        with self._lock:
            for machineId in list(self.runningMachines):
                if machineId not in machineIds:
                    self.runningMachines[machineId].stop(join=False)
                    self._machineRemoved(machineId)

            for container in docker.from_env().containers.list(all=True):
                if container.name.startswith(self.config.docker_scope):
                    try:
                        logging.info("LocalMachineManagement shutting down container %s named %s", container, container.name)
                        container.remove(force=True)
                    except:
                        logging.error("LocalMachineManagement failed to remove container %s:\n%s", container, traceback.format_exc())

            return [machineId for machineId in machineIds if machineId not in self.runningMachines]

    def terminate_worker(self, machineId):
        with self._lock:
            if machineId in self.runningMachines:
                self.runningMachines[machineId].stop(join=False)
                self._machineRemoved(machineId)

    def boot_worker(self, hardware_config, os_config):
        with self._lock:
            if hardware_config not in self.all_hardware_configs():
                return None

            if not os_config.matches.LinuxWithDocker:
                return None

            machineId = "worker_" + str(uuid.uuid4()).replace("-","")[:10]

            worker = TestLooperWorker.TestLooperWorker(
                WorkerState.WorkerState(
                    self.config.docker_scope + "_" + machineId,
                    os.path.join(self.config.local_storage_path, machineId),
                    self.source_control,
                    self.artifactStorage,
                    machineId,
                    hardware_config
                    ),
                machineId,
                self.serverPortConfig
                )

            #this can throw
            self._machineBooted(machineId, hardware_config, os_config, worker)

            worker.start()

            return machineId

class AwsMachineManagement(MachineManagement):
    instance_types = {
        't2.small': HardwareConfig.Config(cores=1, ram_gb=2),
        'm5.xlarge': HardwareConfig.Config(cores=4, ram_gb=16),
        'r3.8xlarge': HardwareConfig.Config(cores=32, ram_gb=244)
        }

    def __init__(self, config, serverPortConfig, source_control, artifactStorage):
        MachineManagement.__init__(self, config, serverPortConfig, source_control, artifactStorage)

    def all_hardware_configs(self):
        return sorted(instance_types.keys(), lambda hw: hw.cores)

    def synchronize_workers(self, machineIds):
        with self._lock:
            pass
        

    def terminate_worker(self, machineId):
        with self._lock:
            pass

    def boot_worker(self, hardware_config, os_config):
        with self._lock:
            pass
        



def fromConfig(config, serverPortConfig, source_control, artifactStorage):
    if config.matches.Aws:
        return AwsMachineManagement(config, serverPortConfig, source_control, artifactStorage)
    elif config.matches.Local:
        return LocalMachineManagement(config, serverPortConfig, source_control, artifactStorage)
    elif config.matches.Dummy:
        return DummyMachineManagement(config, serverPortConfig, source_control, artifactStorage)
    else:
        assert False, "Can't instantiate machine management from %s" % config