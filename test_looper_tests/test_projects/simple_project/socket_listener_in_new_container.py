#!/usr/bin/python3
import docker
import sys
import os

imagename = sys.argv[1]
childname = sys.argv[2]
port = sys.argv[3]
msg = sys.argv[4]

own_dir = os.path.split(__file__)[0]

print(
    "booting image %s with name %s listen to %s/%s" % (imagename, childname, port, msg)
)

container = docker.from_env().containers.run(
    imagename,
    ["python", "/test/socket_listener.py", port, msg],
    volumes={own_dir: "/test"},
    name=childname,
    detach=True,
)

print("child created container ", container)

res = container.wait()

print("Got result", res, container.logs(stdout=True, stderr=True))

if res:
    print("FAILED with code ", res, ": ", container.logs(stdout=True, stderr=True))

sys.stdout.flush()
sys.stderr.flush()
sys.exit(res)
