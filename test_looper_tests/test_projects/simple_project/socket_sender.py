#!/usr/bin/python3

"""
A simple script to send a message on a port

    socket_sender.py HOST PORT MSG

"""

import socket
import sys
import os
import time
import traceback

t0 = time.time()

print("Sending %s to %s:%s" % (sys.argv[3], sys.argv[1], sys.argv[2]))

while True:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((sys.argv[1], int(sys.argv[2])))
        s.send(sys.argv[3] + "\n")
        s.close()
        sys.stdout.flush()
        os._exit(0)
    except:
        time.sleep(0.1)

    if time.time() - t0 > 10.0:
        sys.stdout.flush()
        os._exit(2)
