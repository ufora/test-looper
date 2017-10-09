#!/bin/bash

export PYTHONPATH=/home/braxton/code/test-looper

echo "starting server..."

python -u /home/braxton/code/test-looper/test_looper/server/test-looper-server.py \
	/home/braxton/code/test-looper-install/config.json
	
#> server_log.txt 2>&1 
