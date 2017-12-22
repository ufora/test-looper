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
    def __init__(self, bucket, uuid):
        self.instance = None
        self.bucket = bucket
        self.uuid = uuid

    def wait(self):
        while True:
            t0 = time.time()
            last_print = t0
            log_key = self.bucket.Object("WindowsBootstraps/%s.log" % self.uuid)
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
            self.bucket.put_object(Key="WindowsBootstraps/%s.ps1" % self.uuid, Body="\n".join(text))


    def cleanup(self):
        self.instance.terminate()
        self.bucket.delete_objects(
            Delete={
                'Objects': [
                    {"Key": "WindowsBootstraps/%s.ps1" % self.uuid},
                    {"Key": "WindowsBootstraps/%s.log" % self.uuid}
                    ]
                }
            )

class API:
    def __init__(self, config):
        self.config = config
        self.ec2 = boto3.resource('ec2',region_name=self.config.vpc.region)
        self.s3 = boto3.resource('s3',region_name=self.config.vpc.region)

    def bootWorker(self, 
            path_to_ssh_key,
            source_control_config,
            artifacts_config,
            host_ips,
            platform="linux", 
            instanceType="c3.xlarge",
            clientToken=None
            ):
        assert platform in ["linux", "windows"]

        if platform == "linux":
            boot_script = (
                linux_bootstrap_script
                    .replace("__test_key__", open(path_to_ssh_key,"r").read())
                    .replace("__test_key_pub__", open(path_to_ssh_key + ".pub","r").read())
                    .replace("__test_config__", json.dumps(
                        {
                        "worker": { 
                            "address": self.config.common.server_address, 
                            "port": self.config.common.server_port, 
                            "test_timeout": 900,
                            "use_ssl": True,
                            "scope": "test_looper_dev",
                            "path": "/media/ephemeral0/testlooper/worker"
                            },
                        "source_control": source_control_config,
                        "artifacts": artifacts_config
                        },
                        indent=4
                        )
                    )
                    .replace('__test_looper_https_server__', self.config.common.server_address)
                    .replace('__test_looper_https_port__', str(self.config.common.server_https_port))
                    .replace("__hosts__", "\n\n".join(
                        'echo "%s %s" >> /etc/hosts' % (ip,hostname) for hostname,ip in host_ips.iteritems()
                        )
                    )
                )
            worker = BootedLinuxWorker()
        else:
            bootstrap_uuid = str(uuid.uuid4())

            boot_script = (
                windows_bootstrap_script_invoker
                    .replace("__test_key__", open(path_to_ssh_key,"r").read())
                    .replace("__test_key_pub__", open(path_to_ssh_key + ".pub","r").read())
                    .replace("__bootstrap_bucket__", self.config.common.bucket)
                    .replace("__bootstrap_key__", "WindowsBootstraps/%s.ps1" % bootstrap_uuid)
                    .replace("__bootstrap_log_key__", "WindowsBootstraps/%s.log" % bootstrap_uuid)
                    .replace("__hosts__", "\n\n".join(
                        'echo "%s %s" |  Out-File -Append c:/Windows/System32/Drivers/etc/hosts -Encoding ASCII' % (ip,hostname) for hostname,ip in host_ips.iteritems()
                        ))
                )

            bucket = self.s3.Bucket(self.config.common.bucket)

            bucket.put_object(Key='WindowsBootstraps/%s.ps1' % bootstrap_uuid, Body=windows_bootstrap_script)

            worker = BootedWindowsWorker(bucket, bootstrap_uuid)

        if clientToken is None:
            clientToken = str(uuid.uuid4())
        os_config = self.config.linux if platform == "linux" else self.config.windows

        worker.instance = self.ec2.create_instances(
            ImageId=os_config.base_ami,
            InstanceType=instanceType,
            KeyName=self.config.vpc.keypair,
            MaxCount=1,
            MinCount=1,
            SecurityGroupIds=[self.config.vpc.security_group],
            SubnetId=self.config.vpc.subnet,
            ClientToken=clientToken,
            InstanceInitiatedShutdownBehavior='terminate',
            IamInstanceProfile={'Name': self.config.common.worker_iam_role_name},
            UserData=base64.b64encode(boot_script) if platform=="linux" else boot_script,
            BlockDeviceMappings=[{
                'DeviceName': '/dev/xvdb',
                'VirtualName': "ephemeral0"
                }],
            TagSpecifications=[
                {
                    'ResourceType': 'instance',
                    'Tags': [{ "Key": 'Name', "Value": 'test-looper-worker'}]
                }]
            )[0]
        
        return worker
