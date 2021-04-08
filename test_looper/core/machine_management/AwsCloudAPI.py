import boto3
import uuid
import test_looper.core.algebraic as algebraic
import test_looper.core.algebraic_to_json as algebraic_to_json
from test_looper.core.hash import sha_hash
import base64
import json
import sys
import time
import os.path
import logging
import traceback
import datetime

own_dir = os.path.split(__file__)[0]

windows_ami_creator = open(os.path.join(own_dir, "bootstraps", "windows_ami_creator.ps1"), "r").read()
windows_bootstrap_script = open(os.path.join(own_dir, "bootstraps", "windows_bootstrap.ps1"), "r").read()
linux_bootstrap_script = open(os.path.join(own_dir, "bootstraps", "linux_bootstrap.sh"), "r").read()

class API:
    def __init__(self, config):
        self.config = config
        self.ec2 = boto3.resource('ec2',region_name=self.config.machine_management.region)
        self.ec2_client = boto3.client('ec2',region_name=self.config.machine_management.region)
        self.s3 = boto3.resource('s3',region_name=self.config.machine_management.region)
        self.s3_client = boto3.client('s3',region_name=self.config.machine_management.region)

    def machineIdsOfAllWorkers(self, producingAmis=False):
        filters = [{  
            'Name': 'tag:Name',
            'Values': [self.config.machine_management.worker_name + ("_ami_creator" if producingAmis else "")]
            }]
        res = []
        for reservations in self.ec2_client.describe_instances(Filters=filters)["Reservations"]:
            for instance in reservations["Instances"]:
                if instance['State']['Name'] == 'running' or producingAmis:
                    res.append(str(instance["InstanceId"]))
        return res

    def isInstanceWeOwn(self, instance):
        #make sure this instance is definitely one we booted.

        if not [t for t in instance.tags if t["Key"] == "Name" and t["Value"] == self.config.machine_management.worker_name]:
            return False

        if instance.subnet is None or instance.security_groups is None:
            return False
            
        if instance.subnet.id != self.config.machine_management.subnet:
            return False

        if not [t for t in instance.security_groups if t['GroupId'] == self.config.machine_management.security_group]:
            return False

        if instance.key_pair.name != self.config.machine_management.keypair:
            return False
        
        return True

    def terminateInstanceById(self, id):
        instance = self.ec2.Instance(id)
        assert self.isInstanceWeOwn(instance)
        logging.info("Terminating AWS instance %s", instance)
        instance.terminate()

    def generateAmiConfigLogUrl(self, baseAmi, setupScriptHash, logType):
        bucket = self.s3.Bucket(self.config.machine_management.bootstrap_bucket)

        log_objects = bucket.objects.filter(Prefix=self.bootstrap_key_root + baseAmi + "_" + setupScriptHash + "_" + logType)

        for log in log_objects:
            Params = {'Bucket': self.config.machine_management.bootstrap_bucket, 'Key': log.key}

            Params["ResponseContentType"] = "text/plain"
            Params["ResponseContentDisposition"] = "inline"

            return self.s3_client.generate_presigned_url(
                    'get_object', 
                    Params = Params, 
                    ExpiresIn = 300
                    )

        if len(object) != 1:
            return None
        return object

    def listWindowsImages(self, availableOnly):
        images = self.ec2.images.filter(Owners=['self'])

        res = {}
        for i in images:
            tags = { t["Key"]: t["Value"] for t in i.tags or [] }

            if (tags.get("testlooper_worker_name","") == self.config.machine_management.worker_name 
                    and "BaseAmi" in tags and "SetupScriptHash" in tags):
                if not availableOnly or i.state == "available":
                    res[tags["BaseAmi"],tags["SetupScriptHash"]] = i

        return res

    def getImageByName(self, name):
        images = self.ec2.images.filter(Filters=[{"Name": "name", "Values": [name]}])

        for i in images:
            return i

    def imagesBeingProducedByWorkers(self):
        res = set()

        images = self.listWindowsImages(availableOnly = False)
        
        for id in self.machineIdsOfAllWorkers(producingAmis=True):
            instance = self.ec2.Instance(id)

            if instance.state["Name"] != "terminated":
                tags = {t["Key"]: t["Value"] for t in instance.tags}

                if 'BaseAmi' in tags and 'SetupScriptHash' in tags:
                    res.add((tags["BaseAmi"], tags["SetupScriptHash"]))

        return res

    def gatherAmis(self):
        instances = {}
        images = self.listWindowsImages(availableOnly = False)
        amiStates = self.listWindowsOsConfigs()

        for id in self.machineIdsOfAllWorkers(producingAmis=True):
            instance = self.ec2.Instance(id)

            instanceState = None
            try:
                instanceState = instance.state["Name"]
            except:
                logging.error("Instance %s produced an error asking for its name.", id)

            if instanceState != "terminated" and instanceState is not None:
                tags = {t["Key"]: t["Value"] for t in instance.tags}

                if 'BaseAmi' in tags and 'SetupScriptHash' in tags:
                    instances[tags["BaseAmi"], tags["SetupScriptHash"]] = instance
                else:
                    logging.error("Invalid instance found: %s. Terminating.", id)
                    try:
                        instance.terminate()
                    except:
                        logging.critical("Failed to terminate instance: %s\n%s", id, traceback.format_exc())

        for ami,hash in set(list(instances.keys()) + list(images.keys())):
            try:
                self.checkAmiStateTransition(ami,hash, instances.get((ami,hash),None), images.get((ami,hash),None), amiStates.get((ami,hash), None))
            except:
                logging.error("Failed to update AMI state transition for %s/%s:\n%s", ami, hash, traceback.format_exc())

    def checkAmiStateTransition(self, baseAmi, scriptHash, creatorInstance, actualImage, bootstrapLogState):
        if actualImage is not None:
            if actualImage.state == "available":
                if creatorInstance and creatorInstance.state["Name"] != "terminated":
                    logging.info(
                        "Ami %s/%s still has an instance %s in state %s. terminating", 
                        baseAmi, scriptHash, creatorInstance, creatorInstance.state["Name"]
                        )
                    creatorInstance.terminate()
        else:
            if creatorInstance.state["Name"] in ("terminated", "shutting-down"):
                return

            if bootstrapLogState == "Failed":
                logging.info("Instance %s produced a failure bootstrap log for %s/%s, so shutting it down", creatorInstance, baseAmi, scriptHash)
                creatorInstance.terminate()

            if creatorInstance.state["Name"] == "stopped":
                logging.info("Ami %s/%s has a stopped instance but no image. Creating one.", baseAmi, scriptHash)
                for v in creatorInstance.volumes.all():
                    devices = []
                    for a in v.attachments:
                        if a.get("Device",None):
                            devices.append(a["Device"])
                    
                    if "/dev/xvdb" in devices:
                        creatorInstance.detach_volume(VolumeId=v.id)
                        v.create_tags(Tags=[
                            {'Key': 'testlooper_worker_name', "Value": self.config.machine_management.worker_name},
                            {'Key': 'testlooper_volume_type', "Value": "image_bootstrap_discardable_storage"}
                            ])

                imageName = self.config.machine_management.worker_name + "_" + baseAmi + "_" + scriptHash
                
                image = self.getImageByName(imageName)
                if not image:
                    logging.info("Creating an image named %s", imageName)
                    image = creatorInstance.create_image(Name=imageName)
                    logging.info("Image named %s has id %s", imageName, image)
                else:
                    logging.info("Image named %s already exists as %s", imageName, image)                

                image.create_tags(Tags=[
                    {"Key": "BaseAmi", "Value": baseAmi},
                    {"Key": "SetupScriptHash", "Value": scriptHash},
                    {"Key": "testlooper_worker_name", "Value": self.config.machine_management.worker_name},
                    ])

                logging.info("AMI %s/%s created as %s", baseAmi, scriptHash, image)
            else:
                upFor = time.time() - self.instanceLaunchTimestamp(creatorInstance)

                #two hour cutoff
                if upFor > 3600 * 2:
                    logging.info("Instance %s has been up for %s seconds, which exceeds the cutoff", creatorInstance, upFor)

                    bucket = self.s3.Bucket(self.config.machine_management.bootstrap_bucket)
                    bucket.put_object(Key=self.bootstrap_key_root + baseAmi + "_" + setupScriptHash + "_BootstrapLog.fail", Body="Timed out...")

                    creatorInstance.terminate()
                    return

    def instanceLaunchTimestamp(self, instance):
        launch_naive  = instance.launch_time.replace(tzinfo=None) - instance.launch_time.utcoffset()
        return (launch_naive - datetime.datetime(1970, 1, 1)).total_seconds()

    def listWindowsOsConfigs(self):
        images = self.listWindowsImages(availableOnly=True)
        pending_images = self.listWindowsImages(availableOnly=False)

        bucket = self.s3.Bucket(self.config.machine_management.bootstrap_bucket)

        configs = {}

        for obj in bucket.objects.filter(Prefix=self.bootstrap_key_root):
            key = obj.key[len(self.bootstrap_key_root):]
            try:
                ami, hash, target = key.split("_")

                if (ami,hash) not in configs:
                    configs[ami,hash] = "In progress" 

                if target == "BootstrapLog.fail":
                    configs[ami,hash] = "Failed"

                if target == "BootstrapLog.success":
                    if (ami,hash) in images:
                        configs[ami,hash] = "Complete"
                    elif (ami,hash) in pending_images:
                        configs[ami,hash] = "Snapshotting"
                    else:
                        configs[ami,hash] = "Awaiting snapshot"
            except:
                logging.error("AwsCloudAPI encountered unparsable key %s", key)

        return configs

    def lookupActualAmiForScriptHash(self, baseAmi, setupScriptHash):
        res = self.listWindowsImages(availableOnly=True).get((baseAmi, setupScriptHash), None)
        assert res is not None, "Image %s/%s is not available" % (baseAmi, setupScriptHash)
        return res.id

    @property
    def bootstrap_key_root(self):
        bootstrap_path = self.config.machine_management.bootstrap_key_prefix
        if not bootstrap_path.endswith("/"):
            bootstrap_path += "/"
        return bootstrap_path

    def clearAll(self, dry_run):
        bucket = self.s3.Bucket(self.config.machine_management.bootstrap_bucket)
        for o in bucket.objects.filter(Prefix=self.bootstrap_key_root):
            if not dry_run:
                print "deleting key ", o
                assert False
                o.delete()
            else:
                print "would delete key ", o

        for id in self.machineIdsOfAllWorkers(producingAmis=True):
            instance = self.ec2.Instance(id)
            if instance.state["Name"] in "terminated":
                print instance, " is already terminated."
            else:
                if not dry_run:
                    print "terminating ", instance
                    assert False
                    instance.terminate()
                else:
                    print "would terminate ", instance

        images = self.listWindowsImages(False)
        for ((a,h),i) in images.iteritems():
            if dry_run:
                print "would deregister image ", (a,h), i
            else:
                print "deregister image ", (a,h), i
                assert False
                i.deregister()


    def bootAmiCreator(self, platform, instanceType, baseAmi, setupScript):
        assert platform == "windows"

        setupScriptHash = sha_hash(setupScript).hexdigest

        if (baseAmi, setupScriptHash) in self.imagesBeingProducedByWorkers():
            logging.warn("We tried to boot an image creator for %s/%s, but one is already up", (baseAmi, setupScriptHash))
            return

        if (baseAmi, setupScriptHash) in self.listWindowsImages(availableOnly=True):
            logging.warn("We tried to boot an image creator for %s/%s, but the image already exists!", (baseAmi, setupScriptHash))
            return

        logging.info("Booting an AMI creator for ami %s and hash %s", baseAmi, setupScriptHash)

        to_json = algebraic_to_json.Encoder().to_json

        baseAmi = baseAmi or self.config.machine_management.windows_ami

        looper_server_and_port = (
            "%s:%s" % (
                self.config.server_ports.server_address, 
                self.config.server_ports.server_https_port
                )
            )
        
        bootstrap_path = self.bootstrap_key_root + baseAmi + "_" + sha_hash(setupScript).hexdigest

        boot_script = (
            windows_ami_creator
                .replace("__windows_box_password__", self.config.machine_management.windows_password)
                .replace("__test_key__", open(self.config.machine_management.path_to_keys,"r").read())
                .replace("__test_key_pub__", open(self.config.machine_management.path_to_keys + ".pub","r").read())
                .replace("__bootstrap_bucket__", self.config.machine_management.bootstrap_bucket)
                .replace("__installation_key__", bootstrap_path + "_InstallScript.ps1")
                .replace("__reboot_script_key__", bootstrap_path + "_RebootScript.ps1")
                .replace("__bootstrap_log_key__", bootstrap_path + "_BootstrapLog")
                .replace("__testlooper_server_and_port__", looper_server_and_port)
                .replace("__hosts__", "\n\n".join(
                    'echo "%s %s" |  Out-File -Append c:/Windows/System32/Drivers/etc/hosts -Encoding ASCII' % (ip,hostname) for hostname,ip in 
                        self.config.machine_management.host_ips.iteritems()
                    ))
            )

        bucket = self.s3.Bucket(self.config.machine_management.bootstrap_bucket)

        reboot_script = (
            windows_bootstrap_script.replace("__test_config__", json.dumps({
                    "server_ports": to_json(self.config.server_ports),
                    "artifacts": to_json(self.config.artifacts)
                    }, indent=4))
                .replace("__testlooper_server_and_port__", looper_server_and_port)
            )

        bucket.put_object(Key=bootstrap_path + "_RebootScript.ps1", Body=reboot_script)
        bucket.put_object(Key=bootstrap_path + "_InstallScript.ps1", Body=setupScript)

        self.bootWorker(
            platform, 
            instanceType, 
            amiOverride=baseAmi, 
            bootScriptOverride=boot_script,
            nameValueOverride=self.config.machine_management.worker_name+"_ami_creator",
            extraTags={"BaseAmi": baseAmi, "SetupScriptHash": setupScriptHash},
            wantsTerminateOnShutdown=False
            )

    def bootWorker(self, 
            platform, 
            instanceType,
            clientToken=None,
            amiOverride=None,
            bootScriptOverride=None,
            nameValueOverride=None,
            extraTags=None,
            wantsTerminateOnShutdown=True
            ):
        assert platform in ["linux", "windows"]

        to_json = algebraic_to_json.Encoder().to_json

        if platform == "linux":
            boot_script = (
                linux_bootstrap_script
                    .replace("__test_key__", open(self.config.machine_management.path_to_keys,"r").read())
                    .replace("__test_key_pub__", open(self.config.machine_management.path_to_keys + ".pub","r").read())
                    .replace("__test_config__", json.dumps(
                        {
                        "server_ports": to_json(self.config.server_ports),
                        "artifacts": to_json(self.config.artifacts)
                        },
                        indent=4
                        )
                    )
                    .replace('__test_looper_https_server__', self.config.server_ports.server_address)
                    .replace('__test_looper_https_port__', str(self.config.server_ports.server_https_port))
                    .replace("__hosts__", "\n\n".join(
                        'echo "%s %s" >> /etc/hosts' % (ip,hostname) for hostname,ip in self.config.machine_management.host_ips.iteritems()
                        )
                    )
                )
        else:
            boot_script = ""

        if bootScriptOverride:
            boot_script = bootScriptOverride

        if clientToken is None:
            clientToken = str(uuid.uuid4())

        if amiOverride is not None:
            ami = amiOverride
        elif platform == "linux":
            ami = self.config.machine_management.linux_ami
        elif platform == "Windows":
            ami = self.config.machine_management.windows_ami
        else:
            assert False

        def has_ephemeral_storage(instanceType):
            for t in ['m3', 'c3', 'x1', 'r3', 'f1', 'h1', 'i3', 'd2']:
                if instanceType.startswith(t):
                    return True
            return False

        if has_ephemeral_storage(instanceType):
            deviceMapping = {
                'DeviceName': '/dev/xvdb',
                'VirtualName': "ephemeral0"
                }
        else:
            deviceMapping = {
                'DeviceName': '/dev/xvdb',
                'VirtualName': "ephemeral0",
                "Ebs": {
                    "Encrypted": False,
                    "DeleteOnTermination": True,
                    "VolumeSize": 200,
                    "VolumeType": "gp2"
                    }
                }

        nameValue = nameValueOverride or self.config.machine_management.worker_name

        return str(self.ec2.create_instances(
            ImageId=ami,
            InstanceType=instanceType,
            KeyName=self.config.machine_management.keypair,
            MaxCount=1,
            MinCount=1,
            SecurityGroupIds=[self.config.machine_management.security_group],
            SubnetId=self.config.machine_management.subnet,
            ClientToken=clientToken,
            InstanceInitiatedShutdownBehavior='terminate' if wantsTerminateOnShutdown else "stop",
            IamInstanceProfile={'Name': self.config.machine_management.worker_iam_role_name},
            UserData=base64.b64encode(boot_script) if platform=="linux" else boot_script,
            BlockDeviceMappings=[deviceMapping],
            TagSpecifications=[
                {
                    'ResourceType': 'instance',
                    'Tags': [{ 
                        "Key": 'Name', 
                        "Value": nameValue
                        }] + [{ "Key": k, "Value": v} for (k,v) in (extraTags or {}).iteritems()]
                }]
            )[0].id)
