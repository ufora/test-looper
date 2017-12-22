import boto3
import uuid
import test_looper.core.algebraic as algebraic
import base64
import json
import sys
import time
import os.path

own_dir = os.path.split(__file__)[0]

windows_bootstrap_script_invoker = open(os.path.join(own_dir, "bootstraps", "windows_bootstrap_invoker.ps1"), "r").read()
windows_bootstrap_script = open(os.path.join(own_dir, "bootstraps", "windows_bootstrap.ps1"), "r").read()
linux_bootstrap_script = open(os.path.join(own_dir, "bootstraps", "linux_bootstrap.sh"), "r").read()

class BootedLinuxWorker:
    def __init__(self):
        self.instance = None

    def wait(self):
        prior = ""
        while True:
            c = self.instance.console_output()
            if "Output" in c:
                data = c["Output"]
                sys.stdout.write(data[len(prior):])
                prior = data
            time.sleep(5.0)


    def cleanup(self):
        self.instance.terminate()

class BootedWindowsWorker:
    def __init__(self, bucket, key_prefix, uuid):
        self.instance = None
        self.bucket = bucket
        self.key_prefix = key_prefix
        self.uuid = uuid

    def wait(self):
        while True:
            t0 = time.time()
            last_print = t0
            log_key = self.bucket.Object("%s/%s.log" % (self.key_prefix, self.uuid))
            while True:
                try:
                    log_key.load()
                    break
                except:
                    pass
                    if time.time() - last_print > 10:
                        print "Elapsed: ", time.time() - t0
                        last_print = time.time()

                time.sleep(1.0)

            print log_key.get()['Body'].read()
            log_key.delete()
            
            print '************ Next command, then GO:'
            text = []
            while True:
                line = raw_input()
                if line.strip() == "GO":
                    print "Command sent..."
                    break
                else:
                    text.append(line)
            self.bucket.put_object(Key="%s/%s.ps1" % (self.key_prefix, self.uuid), Body="\n".join(text))


    def cleanup(self):
        self.instance.terminate()
        self.bucket.delete_objects(
            Delete={
                'Objects': [
                    {"Key": "%s/%s.ps1" % (self.key_prefix, self.uuid)},
                    {"Key": "%s/%s.log" % (self.key_prefix, self.uuid)}
                    ]
                }
            )

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
                res.append(str(instances["InstanceId"]))
        return res

    def isInstanceWeOwn(self, instance):
        #make sure this instance is definitely one we booted.

        if not [t for t in instance.tags if t["Key"] == "Name" and t["Value"] == self.config.machine_management.worker_name]:
            return False

        if t.subnet.id != self.config.machine_management.subnet:
            return False

        if t.security_group != self.config.machine_management.security_group:
            return False

        if t.key_pair.name != self.config.machine_management.keypair:
            return False
        
        return True

    def terminateInstanceById(self, id):
        instance = self.ec2.Instance(id)
        assert self.isInstanceWeOwn(instance)
        instance.terminate()

    def bootWorker(self, 
            platform="linux", 
            instanceType="c3.xlarge",
            clientToken=None,
            amiOverride=None
            ):
        assert platform in ["linux", "windows"]

        if platform == "linux":
            boot_script = (
                linux_bootstrap_script
                    .replace("__test_key__", open(self.config.machine_management.path_to_keys,"r").read())
                    .replace("__test_key_pub__", open(self.config.machine_management.path_to_keys + ".pub","r").read())
                    .replace("__test_config__", json.dumps(
                        {
                        "server_ports": self.config.server_ports,
                        "source_control": self.config.source_control,
                        "artifacts": self.config.artifacts
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

            bucket.put_object(Key=self.config.machine_management.bootstrap_key_prefix + '/%s.ps1' % bootstrap_uuid, Body=windows_bootstrap_script)

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
            BlockDeviceMappings=[{
                'DeviceName': '/dev/xvdb',
                'VirtualName': "ephemeral0"
                }],
            TagSpecifications=[
                {
                    'ResourceType': 'instance',
                    'Tags': [{ 
                        "Key": 'Name', 
                        "Value": self.config.machine_management.worker_name
                        }]
                }]
            )[0].id)
