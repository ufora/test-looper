#!/usr/bin/python
import os
import json
import sys
import random

testDefs = {}

group = sys.argv[1]

for ix in xrange(100):
    failureRate = .9
    if '0' in str(ix):
        failureRate = 1.0
    if '3' in str(ix):
        failureRate = 0.0

    paths = []

    for logfile_ix in xrange(1 if '2' not in str(ix) else 3):
	    path = os.path.join(os.getenv("TEST_OUTPUT_DIR"), "logfile_%s_%s.stdout" % (ix,logfile_ix))
	    with open(path,"w") as f:
	    	print >> f, "log results for %s::test_%d slice %s" % (group, ix, logfile_ix)
	    paths.append(path)

    testDefs["%s::test_%d" % (group, ix)] = {"success": random.random() < failureRate, "logs": paths}

print json.dumps(testDefs, indent=2)