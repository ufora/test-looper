#!/bin/bash

trap 'kill $(jobs -p)' EXIT

PROJ_ROOT=`cd ../..; pwd`

export PYTHONPATH=$PROJ_ROOT

export TEST_LOOPER_INSTALL=$PROJ_ROOT/tests/system_test/test_looper_install

rm -rf $TEST_LOOPER_INSTALL
mkdir $TEST_LOOPER_INSTALL

mkdir -p $TEST_LOOPER_INSTALL/repos/repo1
mkdir -p $TEST_LOOPER_INSTALL/repos/repo2
mkdir $TEST_LOOPER_INSTALL/logs
mkdir $TEST_LOOPER_INSTALL/redis

export GIT_AUTHOR_DATE="1509599720 -0500"
export GIT_COMMITTER_DATE="1509599720 -0500"

(cd $TEST_LOOPER_INSTALL/repos/repo1
 git init .
 cp $PROJ_ROOT/tests/test_projects/simple_project/* -r .
 git add .
 git commit -m "initial commit"
 echo "this is a file" > a_file.txt
 git add .
 git commit -m "second commit"
 )

(cd $TEST_LOOPER_INSTALL/repos/repo2
 git init .
 cp $PROJ_ROOT/tests/test_projects/simple_project/* -r .
 git add .
 git commit -m "initial commit in repo2"
 echo "this is a file in repo2" > a_file_in_repo_2.txt
 git add .
 git commit -m "second commit in repo2"
 )

echo "BOOTING REDIS"
( redis-server --port 1111 \
	--logfile $TEST_LOOPER_INSTALL/redis/log.txt \
	--dbfilename db.rdb \
	--dir $TEST_LOOPER_INSTALL/redis \
	> $TEST_LOOPER_INSTALL/logs/redis_log.txt 2>&1 ) &

echo "BOOTING WORKER"
( python -u $PROJ_ROOT/test_looper/worker/test-looper.py $PROJ_ROOT/tests/system_test/config.json 4 > $TEST_LOOPER_INSTALL/logs/worker_log.txt 2>&1 )&

echo "APP"
( export PYTHONPATH=$PROJ_ROOT; 
  cd $PROJ_ROOT/test_looper/server/wetty; 
  node app.js -p 3000 -c $PROJ_ROOT/tests/system_test/config.json > $TEST_LOOPER_INSTALL/logs/wetty_log.txt 2>&1 
  )&

echo "BOOTING SERVER"
python -u $PROJ_ROOT/test_looper/server/test-looper-server.py $PROJ_ROOT/tests/system_test/config.json
