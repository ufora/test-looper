import sys
import pickle

if __name__ == '__main__':
    sys.path = pickle.loads(bytes.fromhex(sys.argv[3]))

import logging
import traceback
import time
import threading
import os
import queue

import test_looper.core.ManagedThread as ManagedThread
import test_looper.core.SubprocessRunner as SubprocessRunner
import test_looper.core.socket_util as socket_util

BYTE_DATA = b"D"
BYTE_EXCEPTION = b"E"
FORK_START_TIMEOUT = 5.0

class OutOfProcessDownloader:
    """A worker that can answer queries in another process and return their results as strings.

    Queries must be pickleable callables. Clients will either receive an exception or have
    the result passed to them as a file descriptor and a bytecount containing the answer.
    """

    def __init__(self, actuallyRunOutOfProcess, childPipes=None, verbose=True):
        self.hasStarted = False
        self.isChild = False
        self.childSubprocess = None
        self.backgroundThread = None
        self.lock = threading.Lock()
        self.writeQueue = queue.Queue()
        self.actuallyRunOutOfProcess = actuallyRunOutOfProcess
        self.verbose = verbose

        if childPipes is None:
            self.createNewPipes_()
        else:
            self.childWriteFD, self.childReadFD = childPipes
            self.isChild = True

            self.closeAllUnusedFileDescriptors()

    def closeAllUnusedFileDescriptors(self):
        #we need to ensure that we don't hold sockets open that we're not supposed to
        try:
            maxFD = os.sysconf("SC_OPEN_MAX")
        except:
            maxFD = 256


        for fd in range(3, maxFD):
            if fd not in (self.childWriteFD, self.childReadFD):
                try:
                    os.close(fd)
                except:
                    pass

    def createNewPipes_(self):
        self.parentReadFD, self.childWriteFD = os.pipe()
        self.childReadFD, self.parentWriteFD = os.pipe()

    def closeAllPipes_(self):
        os.close(self.parentReadFD)
        os.close(self.parentWriteFD)
        os.close(self.childReadFD)
        os.close(self.childWriteFD)


    def start(self):
        assert not self.hasStarted

        if self.actuallyRunOutOfProcess:
            def onStdout(msg):
                if self.verbose:
                    logging.info("OutOfProcessDownloader Out> %s", msg)

            def onStderr(msg):
                if self.verbose:
                    logging.info("OutOfProcessDownloader Err> %s", msg)

            os.set_inheritable(self.childWriteFD, True)
            os.set_inheritable(self.childReadFD, True)

            self.childSubprocess = SubprocessRunner.SubprocessRunner(
                [sys.executable, 
                    __file__, 
                    str(self.childWriteFD), 
                    str(self.childReadFD),
                    pickle.dumps(sys.path).hex()
                    ],
                onStdout,
                onStderr
                )
            self.childSubprocess.start()
            self.hasStarted = True
        else:
            self.hasStarted = True
            self.backgroundThread = ManagedThread.ManagedThread(target=self.executeChild_)
            self.backgroundThread.start()

    def stop(self):
        with self.lock:
            if self.actuallyRunOutOfProcess:
                self.childSubprocess.stop()
                self.childSubprocess = None
                self.closeAllPipes_()
            else:
                self.writeQueue.put(None)
                self.backgroundThread.join()

                self.closeAllPipes_()

            self.hasStarted = False

    def executeChild_(self):
        logging.info("Child started with %s, %s", self.childWriteFD, self.childReadFD)
        self.hasStarted = True
        self.isChild = True

        try:
            while True:
                isException = None
                outgoingMessage = None

                if self.actuallyRunOutOfProcess:
                    msgSize = socket_util.stringToLong(os.read(self.childReadFD, 4))

                    msg = os.read(self.childReadFD, msgSize)

                    t0 = time.time()
                    callback = None
                    try:
                        callback = pickle.loads(msg)

                        outgoingMessage = callback()
                        isException = False
                    except Exception as e:
                        try:
                            logging.error(
                                "OutOfProcessDownloader caught exception after %s seconds: %s\n" +
                                    "Task was %s",
                                time.time() - t0,
                                traceback.format_exc(),
                                callback
                                )
                        except:
                            logging.error(
                                "OutOfProcessDownloader failed formatting error: %s",
                                traceback.format_exc()
                                )

                        outgoingMessage = str(e).encode('utf8')
                        isException = True
                else:
                    t0 = time.time()
                    callback = None

                    callback = self.writeQueue.get()
                    if callback is None:
                        #graceful shutdown message
                        return

                    try:
                        outgoingMessage = callback()
                        isException = False
                    except Exception as e:
                        try:
                            logging.error(
                                "OutOfProcessDownloader caught exception after %s seconds: %s\n" +
                                    "Task was %s",
                                time.time() - t0,
                                traceback.format_exc(),
                                callback
                                )
                        except:
                            logging.error(
                                "OutOfProcessDownloader failed formatting error: %s",
                                traceback.format_exc()
                                )

                        outgoingMessage = str(e).encode('utf8')
                        isException = True


                finalValueToWrite = (
                    (BYTE_EXCEPTION if isException else BYTE_DATA) +
                        socket_util.longToString(len(outgoingMessage)) + outgoingMessage
                    )

                os.write(self.childWriteFD, finalValueToWrite)
        except KeyboardInterrupt as e:
            self.executeChild__()
        except:
            logging.error("Main OutOfProcessDownloader loop failed: %s\n%s", traceback.format_exc())
        finally:
            #bail
            if self.actuallyRunOutOfProcess:
                logging.error("OutOfProcessDownloader exiting")
                os._exit(0)

    def executeAndCallback(self, toExecute, callbackTakingFDAndSize):
        with self.lock:
            assert self.hasStarted

            if self.actuallyRunOutOfProcess:
                toSend = pickle.dumps(toExecute)

                os.write(self.parentWriteFD, socket_util.longToString(len(toSend)))
                os.write(self.parentWriteFD, toSend)
            else:
                self.writeQueue.put(toExecute)

            prefix = os.read(self.parentReadFD, 5)

            assert prefix[:1] in (BYTE_EXCEPTION, BYTE_DATA), prefix
            isException = prefix[:1] == BYTE_EXCEPTION

            msgSize = socket_util.stringToLong(prefix[1:5])

            if isException:
                pickledException = os.read(self.parentReadFD, msgSize)
                raise Exception(pickledException.decode('utf8'))
            else:
                callbackTakingFDAndSize(self.parentReadFD, msgSize)


class OutOfProcessDownloaderPool:
    """Models a pool of out-of-process-downloaders"""
    def __init__(self, maxProcesses, actuallyRunOutOfProcess=True):
        self.downloadersQueue = queue.Queue()

        self.allDownloaders = []

        for _ in range(maxProcesses):
            downloader = OutOfProcessDownloader(
                actuallyRunOutOfProcess
                )

            downloader.start()
            self.downloadersQueue.put(downloader)

            self.allDownloaders.append(downloader)

    def getDownloader(self):
        return OutOfProcessDownloadProxy(self)

    def checkoutDownloader_(self):
        return self.downloadersQueue.get()

    def checkinDownloader_(self, downloader):
        self.downloadersQueue.put(downloader)

    def teardown(self):
        for d in self.allDownloaders:
            d.stop()

    def executeAndReturnResultAsString(self, callback):
        proxy = self.getDownloader()

        result = []

        def cb(s):
            result.append(s)

        proxy.executeAndCallbackWithString(callback, cb)

        assert result, "The callback should have populated the result."

        return result[0]


class OutOfProcessDownloadProxy:
    """Class that checks out a downloader and executes the result"""
    def __init__(self, pool):
        self.pool = pool

    def executeAndCallbackWithFileDescriptor(self, toExecute, callbackTakingFDAndSize):
        """Execute 'toExecute' in another process and pass a filedescriptor and size to the callback.

        If the remote process encounters an exception, we raise that immediately.
        """
        d = self.pool.checkoutDownloader_()

        try:
            d.executeAndCallback(toExecute, callbackTakingFDAndSize)
        finally:
            self.pool.checkinDownloader_(d)

    def executeAndCallbackWithString(self, toExecute, callbackTakingString):
        """Execute 'toExecute' in another process and pass the resulting string to the callback.

        If the remote process encounters an exception, we raise that immediately.
        """
        def callbackTakingFDAndSize(fileDescriptor, sz):
            callbackTakingString(os.read(fileDescriptor, sz))

        self.executeAndCallbackWithFileDescriptor(toExecute, callbackTakingFDAndSize)



def main(argv):
    runner = OutOfProcessDownloader(True, (int(argv[1]), int(argv[2])))
    runner.executeChild_()

if __name__ == '__main__':
    main(sys.argv)
