#!/usr/bin/python3
import socket
import sys
import os
import threading
import time

"""
A simple script to receive a message on a port

    socket_listener.py PORT MSG

Listens on PORT and waits to get "MSG" at which point it exits happily.

This is test code, so we exit(1) if we exceed 10 seconds without getting a message.
"""


def killer():
    time.sleep(10)
    print("Didn't get a message in 10 seconds. Exiting.")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)


t = threading.Thread(target=killer)
t.daemon = True
t.start()

target = sys.argv[2]

print("listening on port %s for msg %s" % (sys.argv[1], target))

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(("", int(sys.argv[1])))
s.listen(1)
conn, addr = s.accept()

data = ""
while data[-1:] != "\n":
    data += conn.recv(1)

data = data[:-1]

if data == target:
    sys.stdout.flush()
    os._exit(0)
else:
    print("ERROR: received %s, expecting %s." % (data, target))
    sys.stdout.flush()
    os._exit(1)
