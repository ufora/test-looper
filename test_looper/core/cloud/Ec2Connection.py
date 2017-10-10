import boto.ec2
import collections
import datetime
import itertools
import time
import sys
import logging

image_builder_security_group = 'dev-security-group'

looper_image_name_prefix = 'test-looper-small'
image_builder_tag = 'test-looper-image-builder'
looper_current_image_tag = 'current'
all_states_except_terminated = ['pending', 'running', 'shutting-down', 'stopping', 'stopped']

class cloudSettings:
    def __init__(self,
                aws_region,
                security_group,
                instance_profile_name,
                vpc_subnets,
                worker_ami,
                worker_alt_ami,
                alt_ami_instance_types,
                root_volume_size_gb,
                worker_ssh_key_name,
                worker_user_data,
                test_result_bucket,
                object_tags
                ):
        self.aws_region = aws_region
        self.security_group = security_group
        self.instance_profile_name = instance_profile_name
        self.vpc_subnets = vpc_subnets
        self.worker_ami = worker_ami
        self.worker_alt_ami = worker_alt_ami
        self.alt_ami_instance_types = alt_ami_instance_types
        self.root_volume_size_gb = root_volume_size_gb
        self.worker_ssh_key_name = worker_ssh_key_name
        self.worker_user_data = worker_user_data
        self.test_result_bucket = test_result_bucket
        self.object_tags = object_tags

class TimeoutException(Exception):
    pass


class Ec2Connection(object):
    available_instance_types_and_core_count = [
        ('c3.xlarge', 4),
        ('c3.8xlarge', 32),
        ('g2.2xlarge', 8),
        ('g2.8xlarge', 32)
        ]

    @staticmethod
    def fromConfig(config):
        ownInternalIpAddress = getInstancePrivateIp()
        worker_config_file = config['worker']['config_file']
        worker_core_dump_dir = config['worker']['core_dump_dir']
        worker_user_account = 'test-looper'
        worker_data_dir = '/home/test-looper/test_data'
        worker_ccache_dir = '/home/test-looper/ccache'
        worker_build_cache_dir = '/home/test-looper/build_cache'
        mnt_root_dir = '/mnt/test-looper'
        looperUserData = '''#!/bin/bash
        mount | grep /mnt > /dev/null
        if [ $? -eq 0 ]; then
            UMOUNT_ATTEMPTS=0
            until umount /mnt || [ $UMOUNT_ATTEMPTS -eq 4 ]; do
                echo "Unmount attempt: $(( UMOUNT_ATTEMPTS++ ))"
                fuser -vm /mnt
                sleep 1
            done
            mkfs.btrfs -f /dev/xvdb
            mount /mnt
            start docker
            echo "{worker_core_dump_dir}/core.%p" > /proc/sys/kernel/core_pattern
            mkdir -p {mnt_root_dir}/test_data {mnt_root_dir}/ccache {mnt_root_dir}/build_cache
            chown -R {worker_user_account}:{worker_user_account} {mnt_root_dir}
            mount -B {mnt_root_dir}/test_data {worker_data_dir}
            mount -B {mnt_root_dir}/ccache {worker_ccache_dir}
            mount -B {mnt_root_dir}/build_cache {worker_build_cache_dir}
        fi
        sed -i 's/__PRIVATE_IP__/{server_ip}/' {config_file}
        sed -i 's/__PORT__/{server_port}/' {config_file}
        start test-looper'''.format(
            worker_core_dump_dir=worker_core_dump_dir,
            mnt_root_dir=mnt_root_dir,
            worker_user_account=worker_user_account,
            worker_data_dir=worker_data_dir,
            worker_build_cache_dir=worker_build_cache_dir,
            worker_ccache_dir=worker_ccache_dir,
            server_ip=ownInternalIpAddress,
            server_port=port,
            config_file=worker_config_file
            )

        security_group = config['cloud']['security_group']
        worker_ami = config['cloud']['ami']
        instance_profile_name = config['cloud']['worker_role_name'] or 'test-looper'
        ssh_key_name = config['cloud']['worker_ssh_key_name'] or 'test-looper'
        root_volume_size = config['cloud']['worker_root_volume_size_gb'] or 8
        test_result_bucket = config['cloud']['test_result_bucket']
        vpc_subnets = config['cloud']['vpc_subnets'] or {
            'us-west-2a': 'subnet-112c9266',
            'us-west-2b': 'subnet-9046def5',
            'us-west-2c': 'subnet-7124f928'
            }
        alt_ami_instance_types = []
        worker_alt_ami = config['cloud'].get('alt_ami')
        if worker_alt_ami:
            alt_ami_instance_types = config['cloud'].get('alt_ami_instance_types', [])
        cloudSettings = cloudConnection.cloudSettings(
            aws_region=getInstanceRegion(),
            security_group=security_group,
            instance_profile_name=instance_profile_name,
            vpc_subnets=vpc_subnets,
            worker_ami=worker_ami,
            worker_alt_ami=worker_alt_ami,
            alt_ami_instance_types=alt_ami_instance_types,
            root_volume_size_gb=root_volume_size,
            worker_ssh_key_name=ssh_key_name,
            worker_user_data=looperUserData,
            test_result_bucket=test_result_bucket,
            object_tags=config['cloud'].get('object_tags', {})
            )

        return cloudConnection.cloudConnection(cloudSettings)



    def __init__(self, cloudSettings):
        logging.info("cloud settings: %s", cloudSettings)
        self.ec2Settings = cloudSettings

        if cloudSettings.aws_region is None:
            self.ec2 = boto.connect_ec2()
        else:
            self.ec2 = boto.ec2.connect_to_region(cloudSettings.aws_region)


    def openTestResultBucket(self):
        s3 = boto.connect_s3()
        return s3.get_bucket(self.ec2Settings.test_result_bucket)


    def getLooperInstances(self, ids=None):
        reservations = self.ec2.get_all_instances(
            ids,
            {
                'instance.group-id': self.ec2Settings.security_group,
                'instance-state-name': all_states_except_terminated
            })
        return list(itertools.chain(*[res.instances for res in reservations]))


    def getLooperByAddress(self, address):
        match = [inst for inst in self.getLooperInstances()
                 if address in (inst.ip_address, inst.private_ip_address)]
        return match[0] if match else None


    def isMachineAlive(self, address):
        return self.getLooperByAddress(address) is not None


    def tagInstance(self, address):
        instance = self.getLooperByAddress(address)
        if instance:
            ids_to_tag = [instance.id] + [
                bd.volume_id for bd in instance.block_device_mapping.itervalues()
                ]
            self.ec2.create_tags(ids_to_tag, self.ec2Settings.object_tags)


    def getLooperSpotRequests(self):
        def isLooperRequest(spotRequest):
            return any(g for g in spotRequest.launch_specification.groups
                       if g.id == self.ec2Settings.security_group)
        return {
            req.id : req
            for req in self.ec2.get_all_spot_instance_requests()
            if isLooperRequest(req)
            }


    def getAllSpotRequestObjects(self):
        return self.getLooperSpotRequests().itervalues()


    def cancelSpotRequests(self, requestIds):
        if len(requestIds) == 0:
            return
        spotRequests = self.ec2.get_all_spot_instance_requests(requestIds)
        instanceIds = [r.instance_id for r in spotRequests if r.state == 'active']
        terminated = self.ec2.cancel_spot_instance_requests(requestIds)
        logging.info("Terminated instances: %s", terminated)
        if len(instanceIds) > 0:
            self.terminateInstances(instanceIds)


    def currentSpotPrices(self, instanceType=None):
        now = datetime.datetime.utcnow().isoformat()
        prices = self.ec2.get_spot_price_history(start_time=now,
                                                 end_time=now,
                                                 instance_type=instanceType)
        pricesByZone = {}
        for p in prices:
            if p.availability_zone not in pricesByZone:
                pricesByZone[p.availability_zone] = p.price
        return pricesByZone


    def terminateInstances(self, instanceIds):
        print "Terminating instances:", instanceIds
        return self.ec2.terminate_instances(instanceIds)


    def getLooperImages(self, ids=None, filters=None):
        allFilters = {'name': looper_image_name_prefix + '*'}
        if filters is not None:
            assert isinstance(filters, dict)
            allFilters.update(filters)
        return self.ec2.get_all_images(image_ids=ids, filters=allFilters)


    def saveImage(self, instanceId, namePrefix):
        name = self.makeImageName(namePrefix)
        return self.ec2.create_image(instanceId, name)


    def makeImageName(self, namePrefix):
        today = str(datetime.date.today())
        namePattern = "%s-%s*" % (namePrefix, today)
        existingImages = self.ec2.get_all_images(owners=['self'],
                                                 filters={'name': namePattern})
        if len(existingImages) == 0:
            name = namePattern[:-1]
        else:
            name = "%s-%s-%s" % (namePrefix, today, len(existingImages))
        return name


    def waitForImage(self, imageId, timeout=300):
        t0 = time.time()
        sys.stdout.write("Waiting for image %s" % imageId)
        sys.stdout.flush()
        try:
            while True:
                try:
                    images = self.getLooperImages(ids=[imageId])
                except boto.exception.cloudResponseError:
                    images = []
                if len(images) == 1:
                    if images[0].state == u'available':
                        return
                    if images[0].state != u'pending':
                        print "Image is in unexpected state:", images[0].state
                        raise Exception("Unexpected image state")
                sys.stdout.write('.')
                sys.stdout.flush()
                time.sleep(2)
                if time.time() - t0 > timeout:
                    raise TimeoutException()
        finally:
            print ""


    def image_id_for_instance_type(self, instance_type):
        if instance_type in self.ec2Settings.alt_ami_instance_types:
            assert self.ec2Settings.worker_alt_ami is not None
            return self.ec2Settings.worker_alt_ami

        return self.ec2Settings.worker_ami


    def requestLooperInstances(self,
                               max_bid,
                               instance_type="m3.xlarge",
                               instance_count=1,
                               launch_group=None,
                               availability_zone=None):
        logging.info(
            ("cloud connection, request spot instance_type: %s, max bid: %s, instance count: %s, "
             "launch_group: %s, availability_zone: %s"),
            instance_type, max_bid, instance_count, launch_group, availability_zone
            )
        ami = self.image_id_for_instance_type(instance_type)
        block_device_map = self.createBlockDeviceMapping()
        subnet_id = self.getVpcSubnetForInstance(availability_zone, instance_type)
        spot_requests = self.ec2.request_spot_instances(
            image_id=ami,
            price=max_bid,
            instance_type=instance_type,
            count=instance_count,
            launch_group=launch_group,
            subnet_id=subnet_id,
            block_device_map=block_device_map,
            security_group_ids=[self.ec2Settings.security_group],
            key_name=self.ec2Settings.worker_ssh_key_name,
            type='persistent',
            instance_profile_name=self.ec2Settings.instance_profile_name,
            user_data=self.ec2Settings.worker_user_data
            )

        while True:
            try:
                self.ec2.create_tags([r.id for r in spot_requests],
                                     self.ec2Settings.object_tags)
                return
            except boto.exception.cloudResponseError as e:
                if e.body and 'InvalidSpotInstanceRequestID.NotFound' in e.body:
                    time.sleep(1.0)
                else:
                    raise


    def createBlockDeviceMapping(self):
        if self.ec2Settings.root_volume_size_gb is None:
            return None
        dev_sda1 = boto.ec2.blockdevicemapping.EBSBlockDeviceType(
            delete_on_termination=True
            )
        dev_sda1.size = self.ec2Settings.root_volume_size_gb
        bdm = boto.ec2.blockdevicemapping.BlockDeviceMapping()
        bdm['/dev/sda1'] = dev_sda1
        return bdm


    def getVpcSubnetForInstance(self, availability_zone, instance_type):
        if availability_zone is None:
            spot_prices = self.currentSpotPrices(instance_type)
            cheapest_az_and_price = min(spot_prices.iteritems(),
                                        key=lambda az_and_price: az_and_price[1])
            availability_zone = cheapest_az_and_price[0]
        return self.ec2Settings.vpc_subnets[availability_zone]
