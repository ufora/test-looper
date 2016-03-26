#!/usr/bin/env python

import argparse
import boto
import json
import logging
import os
import socket
import sys

import test_looper.server.Github as Github
import test_looper.server.RedisJsonStore as RedisJsonStore
import test_looper.server.TestLooperEc2Connection as TestLooperEc2Connection
import test_looper.server.TestLooperHttpServer as TestLooperHttpServer
import test_looper.server.TestLooperServer as TestLooperServer
import test_looper.server.TestManager as TestManager
import test_looper.server.TestLooperEc2Machines as TestLooperEc2Machines
import test_looper.server.TestLooperAutoProvisioner as TestLooperAutoProvisioner

TEST_LOOPER_APP_CLIENT_ID = "TEST_LOOPER_APP_CLIENT_ID"
TEST_LOOPER_APP_CLIENT_SECRET = "TEST_LOOPER_APP_CLIENT_SECRET"
TEST_LOOPER_GITHUB_ACCESS_TOKEN = "TEST_LOOPER_GITHUB_ACCESS_TOKEN"

def main():
    parsedArgs = createArgumentParser().parse_args()
    config = loadConfiguration(parsedArgs.config)
    configureLogging(verbose=parsedArgs.verbose)
    configureBoto()

    githubAppId = parsedArgs.githubAppId or config['github']['app_id'] \
        if 'github' in config and 'app_id' in config['github'] else \
        os.getenv(TEST_LOOPER_APP_CLIENT_ID)
    if githubAppId is None and not parsedArgs.no_auth:
        logging.critical("Either 'github.app_id' config setting or %s must be set.",
                         TEST_LOOPER_APP_CLIENT_ID)

    githubAppSecret = parsedArgs.githubAppSecret or config['github']['app_secret'] \
        if 'github' in config and 'app_secret' in config['github'] else \
        os.getenv(TEST_LOOPER_APP_CLIENT_SECRET)
    if githubAppSecret is None and not parsedArgs.no_auth:
        logging.critical("Either 'github.app_secret' config setting or %s must be set.",
                         TEST_LOOPER_APP_CLIENT_SECRET)

    githubAccessToken = parsedArgs.githubAccessToken or config['github']['access_token'] \
        if 'github' in config and 'access_token' in config['github'] else \
        os.getenv(TEST_LOOPER_GITHUB_ACCESS_TOKEN)
    if githubAccessToken is None and not parsedArgs.no_auth:
        logging.critical("Either 'github.access_token' config setting or %s must be set.",
                         TEST_LOOPER_GITHUB_ACCESS_TOKEN)

    port = config['server']['port']
    logging.info("Starting test-looper server on port %d", port)
    testManager = TestManager.TestManager(
        Github.Github(githubAppId,
                      githubAppSecret,
                      githubAccessToken,
                      repo=config['github']['target_repo']),
        RedisJsonStore.RedisJsonStore(),
        TestLooperServer.LockWithTimer(),
        TestManager.TestManagerSettings(
            baseline_branch=config['github']['baseline_branch'],
            baseline_depth=config['github'].get('baseline_depth', 20),
            builder_min_cores=config['server'].get('builder_min_cores', 32)
            )
        )

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

    def CreateEc2Connection():
        security_group = config['ec2']['security_group'] or parsedArgs.ec2SecurityGroup
        worker_ami = config['ec2']['ami'] or parsedArgs.looperAmi
        instance_profile_name = config['ec2']['worker_role_name'] or 'test-looper'
        ssh_key_name = config['ec2']['worker_ssh_key_name'] or 'test-looper'
        root_volume_size = config['ec2']['worker_root_volume_size_gb'] or 8
        test_result_bucket = config['ec2']['test_result_bucket']
        vpc_subnets = config['ec2']['vpc_subnets'] or {
            'us-west-2a': 'subnet-112c9266',
            'us-west-2b': 'subnet-9046def5',
            'us-west-2c': 'subnet-7124f928'
            }
        ec2Settings = TestLooperEc2Connection.Ec2Settings(
            aws_region=getInstanceRegion(),
            security_group=security_group,
            instance_profile_name=instance_profile_name,
            vpc_subnets=vpc_subnets,
            worker_ami=worker_ami,
            root_volume_size_gb=root_volume_size,
            worker_ssh_key_name=ssh_key_name,
            worker_user_data=looperUserData,
            test_result_bucket=test_result_bucket
            )

        return TestLooperEc2Connection.EC2Connection(ec2Settings)

    looper_branch = config['github']['test_looper_branch'] or parsedArgs.looperBranch
    github_webhook_secret = str(config['github'].get('webhook_secret')) if 'github' in config \
        else None
    http_port = config['server']['http_port'] or parsedArgs.httpPort

    testLooperMachines = None

    if not parsedArgs.local:
        ec2Connection = CreateEc2Connection()
        testLooperMachines = TestLooperAutoProvisioner.TestLooperAutoProvisioner(
            testManager,
            TestLooperEc2Machines.TestLooperEc2Machines(ec2Connection)
            )

    httpServer = TestLooperHttpServer.TestLooperHttpServer(
        testManager,
        CreateEc2Connection,
        testLooperMachines,
        githubReceivedAPushSecret=github_webhook_secret,
        testLooperBranch=looper_branch,
        httpPortOverride=http_port,
        disableAuth=parsedArgs.no_auth,
        repo=config['github']['target_repo']
        )


    server = TestLooperServer.TestLooperServer(port,
                                               testManager,
                                               httpServer,
                                               testLooperMachines)

    try:
        server.runListenLoop()
    except KeyboardInterrupt:
        pass
    except:
        import traceback
        logging.error(traceback.format_exc())
    server.stop()

def createArgumentParser():
    parser = argparse.ArgumentParser(
        description="Handles test-looper connections and assign test jobs to loopers."
        )

    parser.add_argument('-f', '--config-file',
                        dest='config',
                        required=True,
                        help="test-looper-server configuration file")

    parser.add_argument("--port",
                        dest='port',
                        required=False,
                        type=int,
                        default=7531,
                        help="Listening port")

    parser.add_argument("--httpPort",
                        dest='httpPort',
                        required=False,
                        type=int,
                        help="Port to run http server on")

    parser.add_argument("--githubAppId",
                        dest='githubAppId',
                        required=False,
                        help="OAuth client ID of test-looper GitHub Application. "
                             "Can also be specified using the " + TEST_LOOPER_APP_CLIENT_ID +
                             "environment variable.")

    parser.add_argument("--githubAppSecret",
                        dest='githubAppSecret',
                        required=False,
                        help="OAuth client secret of test-looper GitHub Application. "
                             "Can also be specified using the " + TEST_LOOPER_APP_CLIENT_SECRET +
                             "environment variable.")

    parser.add_argument("--githubAccessToken",
                        dest='githubAccessToken',
                        required=False,
                        help="GitHub access token with 'read' permissions to the main repo"
                             "Can also be specified using the " + TEST_LOOPER_GITHUB_ACCESS_TOKEN +
                             "environment variable.")

    parser.add_argument("-v",
                        "--verbose",
                        action='store_true',
                        required=False)

    parser.add_argument("--local",
                        action='store_true',
                        required=False,
                        help="Run locally without EC2")

    parser.add_argument("--no-auth",
                        action='store_true',
                        required=False,
                        help="Disable authentication")

    parser.add_argument('--ec2SecurityGroup',
                        dest='ec2SecurityGroup',
                        default='looper',
                        required=False,
                        help='EC2 security group to use when launching loopers')

    parser.add_argument('--looperAmi',
                        dest='looperAmi',
                        required=False,
                        help='The EC2 image Id to use when launchin loopers')

    parser.add_argument('--looperBranch',
                        dest='looperBranch',
                        required=False,
                        help='The GitHub branch containing the test-looper codebase')
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


def configureBoto():
    if not boto.config.has_section('Boto'):
        boto.config.add_section('Boto')
    if not boto.config.has_option('Boto', 'metadata_service_timeout'):
        boto.config.set('Boto', 'metadata_service_timeout', '1')
    if not boto.config.has_option('Boto', 'metadata_service_num_attempts'):
        boto.config.set('Boto', 'metadata_service_num_attempts', '1')
    if not boto.config.has_option('Boto', 'http_socket_timeout'):
        boto.config.set('Boto', 'http_socket_timeout', '1')


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
