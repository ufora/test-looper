#!/bin/bash

trap 'kill $(jobs -p)' EXIT

PROJ_ROOT=`cd ../..; pwd`

export PYTHONPATH=$PROJ_ROOT

export TEST_LOOPER_INSTALL=$PROJ_ROOT/test_looper_tests/system_test/test_looper_install

echo "RUNNING WETTY APP"

export PYTHONPATH=$PROJ_ROOT;

cd $PROJ_ROOT/test_looper/server/wetty;

node app.js -c $PROJ_ROOT/test_looper_tests/system_test/config.json


