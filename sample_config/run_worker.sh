#!/bin/bash

export PYTHONPATH=/home/braxton/code/test-looper

echo "starting worker..."

rm -rf /home/braxton/code/test-looper-install/test_looper/worker/test_data/*

python -u /home/braxton/code/test-looper/test_looper/worker/test-looper.py \
	/home/braxton/code/test-looper-install/config.json \
	> worker_log.txt 2>&1
