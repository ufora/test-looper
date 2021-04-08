import test_looper.core.algebraic as algebraic
import test_looper.worker.TestLooperWorker as TestLooperWorker
import test_looper.worker.WorkerState as WorkerState
import test_looper.core.Config as Config
import test_looper.core.machine_management.AwsCloudAPI as AwsCloudAPI
import uuid
import docker
import logging
import threading
import traceback
import os

OsConfig = algebraic.Alternative("OsConfig")
OsConfig.LinuxWithDocker = {}
OsConfig.WindowsWithDocker = {}
OsConfig.WindowsVM = {"ami": str, "setupHash": str}
OsConfig.LinuxVM = {"ami": str, "setupHash": str}


class UnbootableWorkerCombination(Exception):
    """An exception indicating that we can't meet this request for hardware/software,
    either because the AMI doesn't exist, or because we don't support some feature yet.
    """

    def __init__(self, hardwareConfig, osConfig):
        Exception.__init__(
            self, "Can't boot configuration %s/%s" % (hardwareConfig, osConfig)
        )

        self.hardwareConfig = hardwareConfig
        self.osConfig = osConfig


class MachineManagement(object):
    """Base class for 'machine management' which is responsible for booting workers to work on tests."""

    def __init__(self, config, sourceControl, artifactStorage):
        self.config = config
        self.source_control = sourceControl
        self.artifactStorage = artifactStorage

        self.hardwareConfigs = {}
        self.osConfigs = {}
        self.runningMachines = {}

        self.ram_gb_booted = 0
        self.cores_booted = 0

        self.windowsOsConfigsAvailable = set()
        self.windowsOsConfigsBeingCreated = set()
        self.invalidWindowsOsConfigs = set()

        self._lock = threading.RLock()

    def shutdown(self):
        pass

    def amiCollectionCheck(self):
        pass

    def wantsToSeeSetupScriptForOsConfig(self, osConfig):
        return False

    def isOsConfigInvalid(self, osConfig):
        return False

    def amiConfigLogUrl(self, baseAmi, contentHash, type):
        return None

    def ensureOsConfigAvailable(self, osConfig, setupScript):
        assert False, "Not implemented"

    def canBoot(self, hardwareConfig, osConfig):
        with self._lock:
            config = self.config.machine_management

            if not (
                self.ram_gb_booted + hardwareConfig.ram_gb <= config.max_ram_gb
                or config.max_ram_gb <= 0
            ):
                return False

            if not (
                self.cores_booted + hardwareConfig.cores <= config.max_cores
                or config.max_cores <= 0
            ):
                return False

            if not (
                len(self.runningMachines) + 1 <= config.max_workers
                or config.max_workers <= 0
            ):
                return False

            return True

    def _machineBooted(
        self, machineId, hardwareConfig, osConfig, machine, nocheck=False
    ):
        with self._lock:
            if not nocheck:
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

        machineIds: a dict from machineId to (hardwareConfig, osConfig)

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
    def __init__(self, config, sourceControl, artifactStorage):
        MachineManagement.__init__(self, config, sourceControl, artifactStorage)

    def all_hardware_configs(self):
        return [
            Config.HardwareConfig.Config(cores=1, ram_gb=4),
            Config.HardwareConfig.Config(cores=4, ram_gb=16),
        ]

    def synchronize_workers(self, machineIds):
        with self._lock:
            for machineId in list(self.runningMachines):
                if machineId not in machineIds:
                    self._machineRemoved(machineId)

            return [
                machineId
                for machineId in machineIds
                if machineId not in self.runningMachines
            ]

    def terminate_worker(self, machineId):
        with self._lock:
            if machineId in self.runningMachines:
                self._machineRemoved(machineId)
            else:
                assert False, "Don't know about machine %s" % machineId

    def boot_worker(self, hardware_config, os_config):
        with self._lock:
            assert hardware_config in self.all_hardware_configs()

            if os_config.matches.WindowsVM and not os_config.ami.startswith("ami-"):
                raise UnbootableWorkerCombination(hardware_config, os_config)

            machineId = "worker_" + str(uuid.uuid4()).replace("-", "")[:10]

            self._machineBooted(machineId, hardware_config, os_config, True)

            return machineId


class LocalMachineManagement(MachineManagement):
    def __init__(self, config, sourceControl, artifactStorage):
        MachineManagement.__init__(self, config, sourceControl, artifactStorage)
        self.windows_oneshots = 0

    def all_hardware_configs(self):
        return [Config.HardwareConfig.Config(cores=1, ram_gb=4)]

    def synchronize_workers(self, machineIds):
        with self._lock:
            for machineId in list(self.runningMachines):
                if machineId not in machineIds:
                    self.runningMachines[machineId].stop(join=False)
                    self._machineRemoved(machineId)

            for container in docker.from_env().containers.list(all=True):
                if container.name.startswith(
                    self.config.machine_management.docker_scope
                ):
                    try:
                        logging.info(
                            "LocalMachineManagement shutting down container %s named %s",
                            container,
                            container.name,
                        )
                        container.remove(force=True)
                    except:
                        logging.error(
                            "LocalMachineManagement failed to remove container %s:\n%s",
                            container,
                            traceback.format_exc(),
                        )

            return [
                machineId
                for machineId in machineIds
                if machineId not in self.runningMachines
            ]

    def terminate_worker(self, machineId):
        with self._lock:
            if machineId in self.runningMachines:
                self.runningMachines[machineId].stop(join=False)
                self._machineRemoved(machineId)

    def shutdown(self):
        logging.info(
            "LocalMachineManagement shutting down %s workers", len(self.runningMachines)
        )
        for machineId, worker in self.runningMachines.items():
            try:
                logging.info("Initiating worker shutdown for %s", machineId)
                worker.stop()
                logging.info("Completed worker shutdown for %s", machineId)
            except:
                logging.error("Failed to shut down worker %s", machineId)

    def boot_worker(self, hardware_config, os_config):
        with self._lock:
            assert hardware_config in self.all_hardware_configs()

            logging.info("Trying to boot %s / %s", hardware_config, os_config)

            if os_config.matches.WindowsVM:
                machineId = "worker_" + str(self.windows_oneshots)
                logging.info("Booted fake windows one-shot worker %s" % machineId)
                self.windows_oneshots += 1

                self._machineBooted(machineId, hardware_config, os_config, True)

                return machineId
            else:
                if not os_config.matches.LinuxWithDocker:
                    raise UnbootableWorkerCombination(hardware_config, os_config)

                machineId = "worker_" + str(uuid.uuid4()).replace("-", "")[:10]

                worker = TestLooperWorker.TestLooperWorker(
                    WorkerState.WorkerState(
                        self.config.machine_management.docker_scope + "_" + machineId,
                        os.path.join(
                            self.config.machine_management.local_storage_path, machineId
                        ),
                        self.artifactStorage,
                        machineId,
                        hardware_config,
                    ),
                    machineId,
                    self.config.server_ports,
                    False,
                    2.0,
                )

                self._machineBooted(machineId, hardware_config, os_config, worker)

                worker.start()

                logging.info("Worker started: %s", machineId)

                return machineId


class AwsMachineManagement(MachineManagement):
    def __init__(self, config, sourceControl, artifactStorage):
        MachineManagement.__init__(self, config, sourceControl, artifactStorage)
        self.api = AwsCloudAPI.API(config)

        self.instance_types = config.machine_management.instance_types

        self.updateOsConfigsAvailable()

    def updateOsConfigsAvailable(self):
        self.windowsOsConfigsAvailable = set()
        self.windowsOsConfigsBeingCreated = set()
        self.invalidWindowsOsConfigs = set()

        listing = self.api.listWindowsOsConfigs()
        for ((ami, hash), status) in listing.items():
            config = OsConfig.WindowsVM(ami=ami, setupHash=hash)

            if status in ("In progress", "Awaiting snapshot", "Snapshotting"):
                logging.info("Ami %s with hash %s is in-process", ami, hash)
                self.windowsOsConfigsBeingCreated.add(config)
            elif status == "Failed":
                logging.info("Ami %s with hash %s failed", ami, hash)
                self.invalidWindowsOsConfigs.add(config)
            elif status == "Complete":
                logging.info("Ami %s with hash %s is complete", ami, hash)
                self.windowsOsConfigsAvailable.add(config)
            else:
                logging.error(
                    "Ami %s with hash %s has an invalid state: %s", ami, hash, status
                )

    def all_hardware_configs(self):
        return sorted(self.instance_types.keys(), key=lambda hw: hw.cores)

    def canBoot(self, hardwareConfig, osConfig):
        if hardwareConfig not in self.instance_types:
            return False

        if (
            osConfig.matches.WindowsVM
            and osConfig not in self.windowsOsConfigsAvailable
        ):
            return False

        return MachineManagement.canBoot(self, hardwareConfig, osConfig)

    def wantsToSeeSetupScriptForOsConfig(self, osConfig):
        with self._lock:
            if osConfig.matches.WindowsVM:
                if osConfig in self.invalidWindowsOsConfigs:
                    return False
                if osConfig in self.windowsOsConfigsAvailable:
                    return False
                if osConfig not in self.windowsOsConfigsBeingCreated:
                    return True

            return False

    def isOsConfigInvalid(self, osConfig):
        with self._lock:
            return osConfig in self.invalidWindowsOsConfigs

    def amiConfigLogUrl(self, baseAmi, contentHash, type="BootstrapLog"):
        with self._lock:
            return self.api.generateAmiConfigLogUrl(baseAmi, contentHash, type)

    def ensureOsConfigAvailable(self, osConfig, setupScript):
        assert osConfig.matches.WindowsVM
        with self._lock:
            if osConfig in self.windowsOsConfigsBeingCreated:
                return

            self.windowsOsConfigsBeingCreated.add(osConfig)
            self.api.bootAmiCreator("windows", "m5.xlarge", osConfig.ami, setupScript)

    def amiCollectionCheck(self):
        self.api.gatherAmis()
        self.updateOsConfigsAvailable()

    def synchronize_workers(self, machineIds):
        with self._lock:
            activeMachines = set(self.api.machineIdsOfAllWorkers())

            all_machine_ids = set(
                list(activeMachines) + list(machineIds) + list(self.runningMachines)
            )

            for m in all_machine_ids:
                logging.info(
                    "Machine %s: AWS=%s  DB=%s  RUN=%s",
                    m,
                    m in activeMachines,
                    m in machineIds,
                    m in self.runningMachines,
                )

            machinesThatAppearDead = [m for m in machineIds if m not in activeMachines]

            machinesToKill = [m for m in activeMachines if m not in machineIds]

            for m in machinesToKill:
                try:
                    self.api.terminateInstanceById(m)
                except:
                    logging.error(
                        "Failed to terminate instance %s:\n%s",
                        m,
                        traceback.format_exc(),
                    )

            for m in machinesThatAppearDead:
                if m in self.runningMachines:
                    self._machineRemoved(m)

            for m in machineIds:
                if m in activeMachines:
                    logging.info(
                        "Machine %s is already up and running: %s/%s",
                        m,
                        machineIds[m][0],
                        machineIds[m][1],
                    )

                    self._machineBooted(
                        m, machineIds[m][0], machineIds[m][1], True, nocheck=True
                    )

            return machinesThatAppearDead

    def terminate_worker(self, machineId):
        with self._lock:
            if machineId in self.runningMachines:
                self.api.terminateInstanceById(machineId)
                self._machineRemoved(machineId)
            else:
                raise Exception(
                    "Machine %s isn't in our list of running instances" % machineId
                )

    def boot_worker(self, hardware_config, os_config):
        with self._lock:
            assert self.canBoot(hardware_config, os_config)
            assert hardware_config in self.instance_types, "Can't find %s in %s" % (
                hardware_config,
                self.instance_types,
            )

            instance_type = self.instance_types[hardware_config]
            if os_config.matches.LinuxWithDocker:
                platform = "linux"
                amiOverride = None
                encodeBootScript = True
            elif os_config.matches.LinuxVM:
                platform = "linux"
                amiOverride = os_config.ami
                if amiOverride is None:
                    raise UnbootableWorkerCombination(hardware_config, os_config)
                if os_config.setupHash:
                    amiOverride = self.api.lookupActualAmiForScriptHash(
                        amiOverride, os_config.setupHash
                    )
                encodeBootScript = False
            elif os_config.matches.WindowsVM:
                platform = "windows"
                amiOverride = os_config.ami
                if not amiOverride:
                    amiOverride = self.config.machine_management.windows_ami
                amiOverride = self.api.lookupActualAmiForScriptHash(
                    amiOverride, os_config.setupHash
                )
                encodeBootScript = False
            else:
                raise UnbootableWorkerCombination(hardware_config, os_config)

            machineId = self.api.bootWorker(
                platform,
                instance_type,
                amiOverride=amiOverride,
                encodeBootScript=encodeBootScript,
            )

            self._machineBooted(machineId, hardware_config, os_config, True)

            return machineId


def fromConfig(config, sourceControl, artifactStorage):
    if config.machine_management.matches.Aws:
        return AwsMachineManagement(config, sourceControl, artifactStorage)
    elif config.machine_management.matches.Local:
        return LocalMachineManagement(config, sourceControl, artifactStorage)
    elif config.machine_management.matches.Dummy:
        return DummyMachineManagement(config, sourceControl, artifactStorage)
    else:
        assert False, "Can't instantiate machine management from %s" % config
