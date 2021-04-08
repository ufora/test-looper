#!/usr/bin/python3

import os
import sys

print(
    "we are inside 'script.py' which exits with the integer value of its first argument, which is ",
    sys.argv[0],
)
sys.stdout.flush()

os._exit(int(sys.argv[1]))
