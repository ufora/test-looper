#!/usr/bin/env python

import argparse
import boto
import json
import logging
import os
import socket
import sys

import test_looper.server.Bitbucket as Bitbucket
import test_looper.server.Github as Github
import test_looper.server.RedisJsonStore as RedisJsonStore
import test_looper.server.TestLooperEc2Connection as TestLooperEc2Connection
import test_looper.server.TestLooperHttpServer as TestLooperHttpServer
import test_looper.server.TestLooperServer as TestLooperServer
import test_looper.server.TestManager as TestManager
import test_looper.server.TestLooperEc2Machines as TestLooperEc2Machines
import test_looper.server.TestLooperAutoProvisioner as TestLooperAutoProvisioner

TEST_LOOPER_OAUTH_KEY = "TEST_LOOPER_OAUTH_KEY"
TEST_LOOPER_OAUTH_SECRET = "TEST_LOOPER_OAUTH_SECRET"
TEST_LOOPER_GITHUB_ACCESS_TOKEN = "TEST_LOOPER_GITHUB_ACCESS_TOKEN"

def main():
    parsedArgs = createArgumentParser().parse_args()
    config = loadConfiguration(parsedArgs.config)
    configureLogging(verbose=parsedArgs.verbose)
    configureBoto()

    src_ctrl_config = config.get('github') or config.get('bitbucket', {})
    oauth_key = src_ctrl_config.get('oauth_key') or os.getenv(TEST_LOOPER_OAUTH_KEY)
    if oauth_key is None and not parsedArgs.no_auth:
        logging.critical("Either 'oauth.key' config setting or %s must be set.",
                         TEST_LOOPER_OAUTH_KEY)

    oauth_secret = src_ctrl_config.get('oauth_secret') or os.getenv(TEST_LOOPER_OAUTH_SECRET)
    if oauth_secret is None and not parsedArgs.no_auth:
        logging.critical("Either 'oauth.secret' config setting or %s must be set.",
                         TEST_LOOPER_OAUTH_SECRET)

    github_access_token = src_ctrl_config.get('access_token') or \
        os.getenv(TEST_LOOPER_GITHUB_ACCESS_TOKEN)
    if github_access_token is None and not parsedArgs.no_auth:
        logging.critical("Either 'github.access_token' config setting or %s must be set.",
                         TEST_LOOPER_GITHUB_ACCESS_TOKEN)

    port = config['server']['port']
    logging.info("Starting test-looper server on port %d", port)

    src_ctrl_args = {
        'oauth_key': oauth_key,
        'oauth_secret': oauth_secret,
        'owner': src_ctrl_config['target_repo_owner'],
        'repo': src_ctrl_config['target_repo'],
        'test_definitions_path': src_ctrl_config['test_definitions_path']
        }
    if 'github' in config:
        src_ctrl_class = Github.Github
        src_ctrl_args['access_token'] = github_access_token
    elif 'bitbucket' in config:
        src_ctrl_class = Bitbucket.Bitbucket
    else:
        logging.critical("No 'github' or 'bitbucket' sections in config")
    src_ctrl = src_ctrl_class(**src_ctrl_args)

    testManager = TestManager.TestManager(
        src_ctrl,
        RedisJsonStore.RedisJsonStore(),
        TestLooperServer.LockWithTimer(),
        TestManager.TestManagerSettings(
            baseline_branch=src_ctrl_config['baseline_branch'],
            baseline_depth=src_ctrl_config.get('baseline_depth', 20),
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
            test_result_bucket=test_result_bucket
            )

        return TestLooperEc2Connection.EC2Connection(ec2Settings)

    looper_branch = src_ctrl_config['test_looper_branch']
    github_webhook_secret = src_ctrl_config.get('webhook_secret')
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
        src_ctrl,
        githubReceivedAPushSecret=github_webhook_secret,
        testLooperBranch=looper_branch,
        httpPortOverride=http_port,
        disableAuth=parsedArgs.no_auth
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

    parser.add_argument('config',
                        help="Path to configuration file")

    parser.add_argument("--port",
                        metavar='N',
                        type=int,
                        default=7531,
                        help="Listening port")

    parser.add_argument("--httpPort",
                        metavar='N',
                        type=int,
                        help="Port to run http server on")

    parser.add_argument("-v",
                        "--verbose",
                        action='store_true',
                        help="Set logging level to verbose")

    parser.add_argument("--local",
                        action='store_true',
                        help="Run locally without EC2")

    parser.add_argument("--no-auth",
                        action='store_true',
                        help="Disable authentication")



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
