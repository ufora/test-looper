#!/usr/bin/env python

import argparse
import boto
import json
import logging
import os
import signal
import socket
import sys
import threading
import time

import test_looper.server.source_control.SourceControlFromConfig as SourceControlFromConfig
from test_looper.server.RedisJsonStore import RedisJsonStore
from test_looper.server.TestDatabase import TestDatabase
import test_looper.server.TestLooperEc2Connection as TestLooperEc2Connection
import test_looper.server.TestLooperHttpServer as TestLooperHttpServer
from test_looper.server.TestLooperHttpServerEventLog import TestLooperHttpServerEventLog
import test_looper.server.TestLooperServer as TestLooperServer
import test_looper.server.TestManager as TestManager
import test_looper.core.ArtifactStorage as ArtifactStorage
TEST_LOOPER_OAUTH_KEY = "TEST_LOOPER_OAUTH_KEY"
TEST_LOOPER_OAUTH_SECRET = "TEST_LOOPER_OAUTH_SECRET"
TEST_LOOPER_GITHUB_ACCESS_TOKEN = "TEST_LOOPER_GITHUB_ACCESS_TOKEN"

available_instance_types_and_core_count = [
    ('c3.xlarge', 4),
    ('c3.8xlarge', 32),
    ('g2.2xlarge', 8),
    ('g2.8xlarge', 32)
    ]


def configureBoto():
    if not boto.config.has_section('Boto'):
        boto.config.add_section('Boto')
    if not boto.config.has_option('Boto', 'metadata_service_timeout'):
        boto.config.set('Boto', 'metadata_service_timeout', '1')
    if not boto.config.has_option('Boto', 'metadata_service_num_attempts'):
        boto.config.set('Boto', 'metadata_service_num_attempts', '1')
    if not boto.config.has_option('Boto', 'http_socket_timeout'):
        boto.config.set('Boto', 'http_socket_timeout', '1')

def main():
    parsedArgs = createArgumentParser().parse_args()
    config = loadConfiguration(parsedArgs.config)
    configureLogging(verbose=parsedArgs.verbose)
    configureBoto()

    port = config['server']['port']
    logging.info("Starting test-looper server on port %d", port)

    src_ctrl = SourceControlFromConfig.getFromConfig(config["source_control"])

    testManager = TestManager.TestManager(
        src_ctrl,
        TestDatabase(RedisJsonStore(), config['server']['redis_prefix']),
        TestLooperServer.LockWithTimer(),
        TestManager.TestManagerSettings(
            baseline_branch=config['server'].get('baseline_branch', 'master'),
            baseline_depth=config['server'].get('baseline_depth', 20),
            max_test_count=config['server'].get('max_test_count', 3),
            test_definitions_default=config.get('test_definitions_default', {})
            )
        )

    def CreateEc2Connection():
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

        security_group = config['ec2']['security_group']
        worker_ami = config['ec2']['ami']
        instance_profile_name = config['ec2']['worker_role_name'] or 'test-looper'
        ssh_key_name = config['ec2']['worker_ssh_key_name'] or 'test-looper'
        root_volume_size = config['ec2']['worker_root_volume_size_gb'] or 8
        test_result_bucket = config['ec2']['test_result_bucket']
        vpc_subnets = config['ec2']['vpc_subnets'] or {
            'us-west-2a': 'subnet-112c9266',
            'us-west-2b': 'subnet-9046def5',
            'us-west-2c': 'subnet-7124f928'
            }
        alt_ami_instance_types = []
        worker_alt_ami = config['ec2'].get('alt_ami')
        if worker_alt_ami:
            alt_ami_instance_types = config['ec2'].get('alt_ami_instance_types', [])
        ec2Settings = TestLooperEc2Connection.Ec2Settings(
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
            object_tags=config['ec2'].get('object_tags', {})
            )

        return TestLooperEc2Connection.EC2Connection(ec2Settings)

    http_port = config['server']['http_port']

    if 'ec2' in config:
        ec2_connection = CreateEc2Connection()
    else:
        ec2_connection = None

    httpServer = TestLooperHttpServer.TestLooperHttpServer(
        testManager,
        ec2_connection,
        ArtifactStorage.storageFromConfig(config['artifacts']),
        available_instance_types_and_core_count,
        src_ctrl,
        str(config['server']['test_looper_webhook_secret']) if 
            'test_looper_webhook_secret' in config['server'] else None,
        event_log=TestLooperHttpServerEventLog(RedisJsonStore()),
        auth_level=parsedArgs.auth,
        testLooperBranch=config['server'].get('looper_branch'),
        httpPortOverride=http_port,
        enable_advanced_views=config['server'].get('enable_advanced_views', False)
        )

    server = TestLooperServer.TestLooperServer(port,
                                               testManager,
                                               httpServer,
                                               ec2_connection
                                               )

    serverThread = threading.Thread(target=server.runListenLoop)
    def handleStopSignal(signum, _):
        logging.info("Signal received: %s. Stopping service.", signum)
        if serverThread and serverThread.isAlive():
            server.stop()
        logging.info("Stopping service complete.")
        os._exit(0)

    signal.signal(signal.SIGTERM, handleStopSignal) # handle kill
    signal.signal(signal.SIGINT, handleStopSignal)  # handle ctrl-c

    serverThread.start()
    while serverThread.is_alive():
        time.sleep(0.5)


def createArgumentParser():
    parser = argparse.ArgumentParser(
        description="Handles test-looper connections and assign test jobs to loopers."
        )

    parser.add_argument('config',
                        help="Path to configuration file")

    parser.add_argument("-v",
                        "--verbose",
                        action='store_true',
                        help="Set logging level to verbose")

    parser.add_argument("--local",
                        action='store_true',
                        help="Run locally without EC2")

    parser.add_argument("--auth",
                        choices=['full', 'write', 'none'],
                        default='full',
                        help=("Authentication requirements.\n"
                              "Full: no unauthenticated access\n"
                              "Write: must authenticate to write\n"
                              "None: open, unauthenticated access"))

    return parser


def loadConfiguration(configFile):
    with open(configFile, 'r') as fin:
        return json.loads(fin.read())


def configureLogging(verbose=False):
    if logging.getLogger().handlers:
        logging.getLogger().handlers = []

    loglevel = logging.DEBUG if verbose else logging.INFO
    logging.getLogger().setLevel(loglevel)

    handler = logging.StreamHandler(stream=sys.stderr)

    handler.setLevel(loglevel)
    handler.setFormatter(
        logging.Formatter(
            '%(asctime)s %(levelname)s %(filename)s:%(lineno)s@%(funcName)s %(name)s - %(message)s'
            )
        )
    logging.getLogger().addHandler(handler)


def getInstanceMetadata():
    return boto.utils.get_instance_metadata(timeout=2.0, num_retries=1)


def getInstancePrivateIp():
    logging.info('Retrieving local IP address from instance metadata')
    metadata = getInstanceMetadata()
    return metadata['local-ipv4'] if metadata else socket.gethostbyname(socket.gethostname())


def getInstanceRegion():
    logging.info('Retrieving availability zone from instance metadata')
    metadata = getInstanceMetadata()
    if not metadata:
        return ''

    placement = metadata['placement']
    az = placement['availability-zone']
    return az[:-1]


if __name__ == "__main__":
    main()
