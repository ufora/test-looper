#!/bin/bash

########################################################################
#this is a bootstrap script to initialize
#a linux test-looper worker that uses docker for process isolation.
#we install some core services, install ssh keys, pull the test-looper codebase from
#a specified host and port, and run the worker.
########################################################################

export STORAGE=/media/ephemeral0

sudo yum install -y docker
sudo yum install -y gcc
sudo yum install -y git

echo "Moving docker directory to $STORAGE"
sudo cp /var/lib/docker $STORAGE -r
sudo rm /var/lib/docker -rf
(cd /var/lib; sudo ln -s $STORAGE/docker)

sudo service docker start

sudo pip install boto3 psutil docker==2.6.1

sudo chmod 777 /var/run/docker.sock

echo "Extending hosts: "

__hosts__

mkdir -p ~/.ssh
echo StrictHostKeyChecking=no >> ~/.ssh/config

echo "Adding keys"

cat > ~/.ssh/id_rsa <<****TEST_KEY****
__test_key__
****TEST_KEY****

cat > ~/.ssh/id_rsa.pub <<****TEST_KEY_PUB****
__test_key_pub__
****TEST_KEY_PUB****


#TEST LOOPER INSTALL
export PYTHONPATH=$STORAGE/testlooper
export TEST_LOOPER_INSTALL=$STORAGE/testlooper

mkdir -p $TEST_LOOPER_INSTALL
mkdir -p $TEST_LOOPER_INSTALL/logs

cd $TEST_LOOPER_INSTALL

chmod 700 -R ~/.ssh

echo "Pulling test-looper source code: "
echo "curl -k https://__test_looper_https_server__:__test_looper_https_port__/test_looper.tar.gz"

curl -k https://__test_looper_https_server__:__test_looper_https_port__/test_looper.tar.gz > test_looper.tar.gz
tar xvf test_looper.tar.gz

echo "Pulling test-looper config file"

cat > worker_config.json <<****TEST_CONFIG****
__test_config__
****TEST_CONFIG****

echo "TestLooper configured as: "
cat worker_config.json

echo "TestLooper starting"
python -u test_looper/worker/test-looper.py worker_config.json 1 > logs/worker_log.txt 2>&1
