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

Ec2Settings = collections.namedtuple('Ec2Settings',
                                     ['aws_region',
                                      'security_group',
                                      'instance_profile_name',
                                      'vpc_subnets', # map from availability zones to subnets
                                      'worker_ami',
                                      'root_volume_size_gb',
                                      'worker_ssh_key_name',
                                      'worker_user_data',
                                      'test_result_bucket'])

class TimeoutException(Exception):
    pass

class EC2Connection(object):
    def __init__(self, ec2Settings):
        logging.info("EC2 settings: %s", ec2Settings)
        self.ec2Settings = ec2Settings

        if ec2Settings.aws_region is None:
            self.ec2 = boto.connect_ec2()
        else:
            self.ec2 = boto.ec2.connect_to_region(ec2Settings.aws_region)

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

    def getLooperSpotRequests(self, includeInactive=False):
        def isLooperRequest(spotRequest):
            if len(filter(lambda g: g.id == self.ec2Settings.security_group,
                          spotRequest.launch_specification.groups)) == 0:
                return False
            return True

        return {req.id : req for req in self.ec2.get_all_spot_instance_requests() if isLooperRequest(req)}

    def getAllSpotRequestObjects(self):
        return self.getLooperSpotRequests().itervalues()

    def cancelSpotRequests(self, requestIds):
        if len(requestIds) == 0:
            return
        spotRequests = self.ec2.get_all_spot_instance_requests(requestIds)
        instanceIds = [r.instance_id for r in spotRequests if r.state == 'active']
        terminated = self.ec2.cancel_spot_instance_requests(requestIds)
        logging.info("Terminated instances: %s" % terminated)
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
        existingImages = self.ec2.get_all_images(
                                            owners=['self'],
                                            filters={'name': namePattern}
                                            )
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
                except boto.exception.EC2ResponseError:
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

    def getCurrentLooperImages(self):
        return self.getLooperImages(filters={'tag-key': looper_current_image_tag})

    def getCurrentLooperImageId(self):
        if self.ec2Settings.worker_ami:
            return self.ec2Settings.worker_ami

        amis = self.getCurrentLooperImages()
        if len(amis) != 1:
            raise Exception("There are %d AMIs with the 'current' tag: %s" % (len(amis), amis))
        return amis[0].id

    def requestLooperInstances(self,
                               max_bid,
                               instance_type="m3.xlarge",
                               instance_count=1,
                               launch_group=None,
                               availability_zone=None):
        logging.info("EC2 connection, request spot instance_type: %s, max bid: %s, instance count: %s, launch_group: %s, availability_zone: %s" %
                (instance_type, max_bid, instance_count, launch_group, availability_zone))
        ami = self.getCurrentLooperImageId()
        block_device_map = self.createBlockDeviceMapping()
        subnet_id = self.getVpcSubnetForInstance(availability_zone, instance_type)
        self.ec2.request_spot_instances(
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

    def startLooperInstance(self, ami, instanceType):
        reservation = self.ec2.run_instances(
                image_id=ami,
                instance_type=instanceType,
                key_name='test_looper',
                security_groups=[image_builder_security_group]
                )
        runningInstances = []
        for instance in reservation.instances:
            print "Launching new instance %s." % instance.id
            instance.add_tag(image_builder_tag)
            if instance.state == 'pending':
                print "New instance %s is in the 'pending' state. Waiting for it to start." % instance.id
            while instance.state == 'pending':
                time.sleep(5)
                instance.update()
            if instance.state != 'running':
                print "Error: New instance %s entered the %s state." % (instance.id, instance.state)
                return
            runningInstances.append(instance)
        return runningInstances

class Images(object):
    def __init__(self, image=None, instanceType=None, instanceId=None, terminateAfterSave=False):
        self.ec2 = EC2Connection()
        self.image = image
        self.instanceType = instanceType
        self.instanceId = instanceId
        self.terminateAfterSave = terminateAfterSave


    def list(self):
        images = sorted(
                self.ec2.getLooperImages(),
                key=lambda image: image.name,
                reverse=True
                )
        print "\n         id        |         name                     |    status "
        print "=" * 68
        for i, image in enumerate(images):
            imageId = image.id
            if 'current' in image.tags:
                imageId = "*" + imageId
            print "%3d. %13s | %s | %s" % (i+1, imageId, image.name.ljust(32), image.state)
        print ""


    def set(self):
        self.setImage(self.image)

    def setImage(self, imageId):
        try:
            newImage = self.ec2.getLooperImages(ids=[imageId])
            assert len(newImage) <= 1, "More than one image has ID %s!" % imageId
        except boto.exception.EC2ResponseError as e:
            print "Error: %s could not be retrieved. %s" % (imageId, e.error_message)
            return

        currentImages = self.ec2.getCurrentLooperImages()
        if len(currentImages) > 1:
            print "Warning: More than image is marked as 'current'"

        for image in currentImages:
            print "Removing 'current' tag from %s: %s" % (image.id, image.name)
            image.remove_tag('current')

        print "Setting 'current' tag on %s" % imageId
        newImage[0].add_tag('current')

    def launch(self):
        image = self.image
        if image is None:
            images = self.ec2.getCurrentLooperImageId()
            image = images[0].id

        print "Launching instance of type %s with image %s" % (self.instanceType, image)
        try:
            instances = self.ec2.startLooperInstance(image, self.instanceType)

            if instances is None:
                return
            print "New instance started at %s" % instances[0].public_dns_name
        except boto.exception.EC2ResponseError as e:
            print "Error: cannot launch instance. %s" % e.error_message
            return

    def save(self):
        imageId = self.ec2.saveImage(self.instanceId, looper_image_name_prefix)
        print "Creating new image:", imageId
        try:
            self.ec2.waitForImage(imageId)
        except TimeoutException:
            print "Timeout exceeded waiting for image to be created."
            return
        self.setImage(imageId)
        if self.terminateAfterSave:
            self.ec2.terminateInstances([self.instanceId])

