'''Stackinfo provides an interface for providing tracebacks of all executing
threads in the system'''

import sys
import traceback
import threading

def getTraces(limit = None):
    '''return a dict of stack traces keyed by thread id for all threads up
    to a certain number of lines determined by "limit"   '''

    aliveThreadIds = set()
    for thread in threading.enumerate():
        aliveThreadIds.add(thread.ident)

    threadDict = {}

    for id, frame in sys._current_frames().items():
        if id in aliveThreadIds:
            threadDict[id] = traceback.format_stack(frame, limit)

    return threadDict


