import logging
import os
import threading
import traceback
import test_looper.core.ThreadLocalStack as ThreadLocalStack
import test_looper.core.StackInfo as StackInfo

# ManagedThread deliberately tracks every unique thread we ever make. We need to keep track of
# them so that we can distinguish them by their IDs
_threadStartedLocation = {}


def nameForIdentity(identity):
    global _threadStartedLocation
    if identity in _threadStartedLocation:
        return _threadStartedLocation[identity].name.ljust(30)
    return str(identity).ljust(30)


class ManagedThread(threading.Thread):
    def __init__(self, *args, **kwargs):
        super(ManagedThread, self).__init__(*args, **kwargs)
        self.daemon = True
        self.criticalErrorHandler = self.logExceptionAndAbort
        self.onCompletion = self.logCompletion
        self.creatorStacktrace = "".join(traceback.format_stack())
        self.creatorThreadLocalStorageDict = (
            ThreadLocalStack.ThreadLocalStack.copyContents()
        )

    @staticmethod
    def allManagedThreads():
        return [
            t
            for t in threading.enumerate()
            if ManagedThread.threadByObjectId(id(t)) is not None
        ]

    @staticmethod
    def threadByObjectId(objectId):
        if objectId in _threadStartedLocation:
            return _threadStartedLocation[objectId]
        return None

    def run(self):
        super(ManagedThread, self)

        ThreadLocalStack.ThreadLocalStack.setContents(
            self.creatorThreadLocalStorageDict
        )

        _threadStartedLocation[id(self)] = self

        try:
            super(ManagedThread, self).run()
        except MemoryError as ex:
            self.criticalErrorHandler(ex)
        except Exception as ex:
            logging.critical(
                "ManagedThread caught exception:\n%s\n\nThread started from\n%s",
                traceback.format_exc(),
                "".join(self.creatorStacktrace),
            )
        finally:
            self.onCompletion()

    def logCompletion(self):
        pass

    @staticmethod
    def logExceptionAndAbort(exception):
        logging.critical(traceback.format_exc())
        os._exit(os.EX_SOFTWARE)

    def __str__(self):
        traces = StackInfo.getTraces()
        if self.ident in traces:
            return "ManagedThread(%s)" % "".join(traces[self.ident])
        else:
            return "ManagedThread(<unknown>)"
