#!/bin/bash

########################################################################
# this is a bootstrap script to initialize
# a linux test-looper worker that uses docker for process isolation.
# we install some core services, install ssh keys, pull the test-looper codebase from
# a specified host and port, and run the worker.
########################################################################

export STORAGE=/media/ephemeral0

echo "Extending hosts: "
__hosts__

machineId=`curl http://169.254.169.254/latest/meta-data/instance-id`

function log() {
	echo "logging: $1 for machine id $machineId"
	curl -k "https://__test_looper_https_server__:__test_looper_https_port__/machineHeartbeatMessage?machineId=$machineId&heartbeatmsg=$1"
}

log "TestLooper%20Mounting%20External%20Storage"

echo "****************"
if [ -b /dev/xvdb ]; then
    echo "Mounting /dev/xvdb to $STORAGE"
    sudo mkfs -t ext4 /dev/xvdb
    sudo mkdir -p $STORAGE
    sudo mount /dev/xvdb $STORAGE
else
    echo "Mounting /dev/nvme1n1 to $STORAGE"
    sudo mkfs -t ext4 /dev/nvme1n1
    sudo mkdir -p $STORAGE
    sudo mount /dev/nvme1n1 $STORAGE
fi

echo "****************"
echo 'df -h $STORAGE'
df -h $STORAGE
echo "****************"

log "TestLooper%20Installing%20Docker"

sudo apt-get install -y docker.io

log "TestLooper%20Installing%20GCC"

sudo apt-get install -y gcc

log "TestLooper%20Installing%20GIT"

sudo apt-get install -y git

sudo apt-get install -y python3
sudo apt-get install -y python3-pip

echo "Moving docker directory to $STORAGE"
sudo cp /var/lib/docker $STORAGE -r
sudo rm /var/lib/docker -rf
(cd /var/lib; sudo ln -s $STORAGE/docker)

log "TestLooper%20Starting%20DOCKER"

sudo service docker start

log "TestLooper%20Installing%20Python%20Dependencies"

sudo pip install boto3 psutil docker==2.6.1

sudo chmod 777 /var/run/docker.sock

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

while true;
do
	log "Executing%20test-looper"

	echo `date`": booting test-looper." >> logs/worker_log.txt
	python -u test_looper/worker/test-looper.py worker_config.json $machineId $TEST_LOOPER_INSTALL >> logs/worker_log.txt 2>&1

	succeeded=0
	while [ $succeeded -eq 0 ];
	do
		echo "re-downloading the looper sourcecode" >> logs/worker_log.txt
		rm -rf test_looper
		rm -rf test_looper.tar.gz

		curl -k https://__test_looper_https_server__:__test_looper_https_port__/test_looper.tar.gz > test_looper.tar.gz
		tar xvf test_looper.tar.gz

		if [ -f test_looper/worker/test-looper.py ];
		then
			succeeded=1
			log "Re-downloaded%20test-looper%20source"
		else
			echo "looper is unavailable. sleeping" >> logs/worker_log.txt
			sleep 2
		fi
	done
done
