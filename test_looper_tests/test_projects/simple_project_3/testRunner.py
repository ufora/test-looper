import sys
import json
import random
import time

if sys.argv[1] == "--list":
    for i in range(100):
        print("Test_%02d" % i)
if sys.argv[1] == "--run":
    with open(sys.argv[2], "r") as f:
        tests = [x.strip() for x in f.readlines()]

    jsonOutput = []

    for test in tests:
        index = int(test[5:])
        if index < 70:
            success = True
        elif index < 90:
            success = random.random() < 0.5
        else:
            success = False

        jsonOutput.append(
            {
                "testName": test,
                "success": success,
                "startTimestamp": time.time(),
                "elapsed": 0.01,
            }
        )
        time.sleep(0.01)

    print(json.dumps(jsonOutput, indent=2))
