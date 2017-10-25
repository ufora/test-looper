#!/bin/bash

trap 'kill -s 9 $(jobs -p)' EXIT

PROJ_ROOT=`cd ../..; pwd`

export PYTHONPATH=$PROJ_ROOT

export TEST_LOOPER_INSTALL=$PROJ_ROOT/tests/system_test/test_looper_install

rm -rf $TEST_LOOPER_INSTALL
mkdir $TEST_LOOPER_INSTALL

mkdir $TEST_LOOPER_INSTALL/repo
mkdir $TEST_LOOPER_INSTALL/logs
mkdir $TEST_LOOPER_INSTALL/redis

(cd $TEST_LOOPER_INSTALL/repo
 git init .
 cp $PROJ_ROOT/tests/test_projects/simple_project/* -r .
 git add .
 git commit -m "initial commit"
 echo "this is a file" > a_file.txt
 git add .
 git commit -m "second commit"
 )

echo "BOOTING REDIS"
( redis-server --port 1111 \
	--logfile $TEST_LOOPER_INSTALL/redis/log.txt \
	--dbfilename db.rdb \
	--dir $TEST_LOOPER_INSTALL/redis \
	> $TEST_LOOPER_INSTALL/logs/redis_log.txt 2>&1 ) &

echo "BOOTING WORKER"
( python -u $PROJ_ROOT/test_looper/worker/test-looper.py $PROJ_ROOT/tests/system_test/config.json > $TEST_LOOPER_INSTALL/logs/worker_log.txt 2>&1 )&

echo "BOOTING SERVER"
python -u $PROJ_ROOT/test_looper/server/test-looper-server.py $PROJ_ROOT/tests/system_test/config.json
