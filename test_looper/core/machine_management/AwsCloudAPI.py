import boto3
import uuid
import test_looper.core.algebraic as algebraic
import test_looper.core.algebraic_to_json as algebraic_to_json
import base64
import json
import sys
import time
import os.path
import logging

own_dir = os.path.split(__file__)[0]

windows_bootstrap_script_invoker = open(os.path.join(own_dir, "bootstraps", "windows_bootstrap_invoker.ps1"), "r").read()
windows_bootstrap_script = open(os.path.join(own_dir, "bootstraps", "windows_bootstrap.ps1"), "r").read()
linux_bootstrap_script = open(os.path.join(own_dir, "bootstraps", "linux_bootstrap.sh"), "r").read()

class API:
    def __init__(self, config):
        self.config = config
        self.ec2 = boto3.resource('ec2',region_name=self.config.machine_management.region)
        self.ec2_client = boto3.client('ec2',region_name=self.config.machine_management.region)
        self.s3 = boto3.resource('s3',region_name=self.config.machine_management.region)

    def machineIdsOfAllWorkers(self):
        filters = [{  
            'Name': 'tag:Name',
            'Values': [self.config.machine_management.worker_name]
            }]
        res = []
        for reservations in self.ec2_client.describe_instances(Filters=filters)["Reservations"]:
            for instance in reservations["Instances"]:
                if instance['State']['Name'] == 'running':
                    res.append(str(instance["InstanceId"]))
        return res

    def isInstanceWeOwn(self, instance):
        #make sure this instance is definitely one we booted.

        if not [t for t in instance.tags if t["Key"] == "Name" and t["Value"] == self.config.machine_management.worker_name]:
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

    def bootWorker(self, 
            platform, 
            instanceType,
            hardwareConfig,
            clientToken=None,
            amiOverride=None
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
                        "source_control": to_json(self.config.source_control),
                        "artifacts": to_json(self.config.artifacts),
                        "cores": hardwareConfig.cores,
                        "ram_gb": hardwareConfig.ram_gb
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
            looper_server_and_port = (
                "%s:%s" % (
                    self.config.server_ports.server_address, 
                    self.config.server_ports.server_https_port
                    )
                )
            bootstrap_uuid = str(uuid.uuid4())

            boot_script = (
                windows_bootstrap_script_invoker
                    .replace("__test_key__", open(self.config.machine_management.path_to_keys,"r").read())
                    .replace("__test_key_pub__", open(self.config.machine_management.path_to_keys + ".pub","r").read())
                    .replace("__bootstrap_bucket__", self.config.machine_management.bootstrap_bucket)
                    .replace("__bootstrap_key__", self.config.machine_management.bootstrap_key_prefix + "/%s.ps1" % bootstrap_uuid)
                    .replace("__bootstrap_log_key__", self.config.machine_management.bootstrap_key_prefix + "/%s.log" % bootstrap_uuid)
                    .replace("__hosts__", "\n\n".join(
                        'echo "%s %s" |  Out-File -Append c:/Windows/System32/Drivers/etc/hosts -Encoding ASCII' % (ip,hostname) for hostname,ip in 
                            self.config.machine_management.host_ips.iteritems()
                        ))
                )

            bucket = self.s3.Bucket(self.config.machine_management.bootstrap_bucket)

            actual_bootstrap_script = (
                windows_bootstrap_script.replace("__test_config__", json.dumps({
                        "server_ports": to_json(self.config.server_ports),
                        "source_control": to_json(self.config.source_control),
                        "artifacts": to_json(self.config.artifacts),
                        "cores": hardwareConfig.cores,
                        "ram_gb": hardwareConfig.ram_gb
                        }, indent=4))
                    .replace("__testlooper_server_and_port__", looper_server_and_port)
                )

            bucket.put_object(Key=self.config.machine_management.bootstrap_key_prefix + '/%s.ps1' % bootstrap_uuid, Body=actual_bootstrap_script)

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
                    "VolumeSize": 100
                    }
                }            

        return str(self.ec2.create_instances(
            ImageId=ami,
            InstanceType=instanceType,
            KeyName=self.config.machine_management.keypair,
            MaxCount=1,
            MinCount=1,
            SecurityGroupIds=[self.config.machine_management.security_group],
            SubnetId=self.config.machine_management.subnet,
            ClientToken=clientToken,
            InstanceInitiatedShutdownBehavior='terminate',
            IamInstanceProfile={'Name': self.config.machine_management.worker_iam_role_name},
            UserData=base64.b64encode(boot_script) if platform=="linux" else boot_script,
            BlockDeviceMappings=[deviceMapping],
            TagSpecifications=[
                {
                    'ResourceType': 'instance',
                    'Tags': [{ 
                        "Key": 'Name', 
                        "Value": self.config.machine_management.worker_name
                        }]
                }]
            )[0].id)
