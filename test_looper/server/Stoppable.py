import threading


class Stoppable(object):
    """provides access to a stop flag across multiple threads"""

    def __init__(self):
        self._stopFlag = self._createStopFlag()

    def _createStopFlag(self):
        return threading.Event()

    def getStopFlag(self):
        return self._stopFlag

    def shouldStop(self):
        return self._stopFlag.is_set()

    def resume(self):
        self._stopFlag.clear()

    def stop(self):
        self._stopFlag.set()
