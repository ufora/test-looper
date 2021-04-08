#!/usr/bin/python3
import os
import json
import sys
import random

testDefs = []

group = sys.argv[1] + "::" if len(sys.argv) == 2 else ""

for passIx in range(3):
    for ix in range(100 if passIx == 0 else 50 if passIx == 1 else 10):
        failureRate = .9
        if '0' in str(ix):
            failureRate = 1.0
        if '3' in str(ix):
            failureRate = 0.0

        paths = []

        for logfile_ix in range(1 if '2' not in str(ix) else 3):
    	    path = os.path.join(os.getenv("TEST_OUTPUT_DIR"), "logfile_%s_%s_%s.stdout" % (ix,logfile_ix,passIx))
    	    with open(path,"w") as f:
    	    	print("log results for %stest_%d slice %s, passIx %s" % (group, ix, logfile_ix, passIx), file=f)
    	    paths.append(path)

        testDefs.append(
            {"testName": "%stest_%d" % (group, ix), "success": random.random() < failureRate, "logs": paths}
            )

print(json.dumps(testDefs, indent=2))
