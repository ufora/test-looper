import struct
import time
import socket
import threading
import logging
import select
import traceback
import sys
import ssl

import test_looper.core.socket_util as socket_util
import test_looper.server.Stoppable as Stoppable

sizeType = '<I'
sizeLength = struct.calcsize(sizeType)

class MessageException(Exception):
    def __init__(self, message):
        Exception.__init__(self, message)

class ServerException(Exception):
    def __init__(self, message):
        Exception.__init__(self, message)

def uintToString(i):
    return struct.pack(sizeType, i)

def stringToUint(s):
    return struct.unpack(sizeType, s)[0]

def prependSizePrefix(s):
    '''take a string and prepend a serialized binary representation of its
    length to the beginning. returns a '''
    return [uintToString(len(s)), s]

def substringUnpacker(s):
    '''reads in a string that contains a number of substrings, each
    of which are preceeded by a binary representation of the substring
    length. It yields each string as it reads it'''

    pos = 0
    while len(s[pos:]):
        assert len(s[pos:]) >= sizeLength
        size = stringToUint(s[pos:pos + sizeLength])
        pos += sizeLength
        toYield = s[pos:pos + size]
        if len(toYield) != size:
            raise MessageException(("corrupt read, size was %s, but string " +
                                    "len was %s") % (size, len(toYield)))

        yield toYield
        pos += size


class SimpleServer(Stoppable.Stoppable):
    '''A simple server that abstracts the process of connecting sockets'''
    def __init__(self, port, nodelay=True, cert_and_key_paths = None):
        Stoppable.Stoppable.__init__(self)
        assert isinstance(port, int), port
        self._port = port
        self._nodelay = nodelay
        self._timeout = 1
        self._started = False
        self._listener = None
        self._socketBindException = None
        self._isListeningEvent = threading.Event()
        self._cert_and_key_paths = cert_and_key_paths

    def bindListener(self, port=None):
        if self._listener is None:
            self._setupListenerSocket(self._port if port is None else port)

        if self._socketBindException is not None:
            logging.error("Failed to bind listener on port %d: %s",
                          self._port,
                          self._socketBindException)
            raise self._socketBindException

    def isListening(self):
        return self._listener is not None

    def blockUntilListening(self):
        while self._listener is None:
            self._isListeningEvent.wait()

            if self._socketBindException is not None:
                logging.error("Failed to bind listener on port %d: %s", self._port, self._socketBindException)
                raise self._socketBindException

    def __del__(self):
        self.stop()

    def _getSocket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if self._cert_and_key_paths:
            return ssl.wrap_socket(
                s,
                server_side=True,
                certfile=self._cert_and_key_paths[0],
                keyfile=self._cert_and_key_paths[1]
                )
        else:
            return s

    def _setupListenerSocket(self, port):
        self._isListeningEvent.clear()

        self._listener = None

        try:
            sock = self._getSocket()
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', port))
            sock.listen(256)
            sock.setblocking(0)
            self._port = port

            self._listener = sock

            self._socketBindException = None

        except Exception as e:
            self._socketBindException = e
            logging.error("Failed to bind listener on port %d: %s", port, e)

        self._isListeningEvent.set()


    def stop(self):
        Stoppable.Stoppable.stop(self)
        if self._listener is not None:
            self._listener.shutdown(socket.SHUT_RDWR)
            self._listener.close()
            self._listener = None

    def start(self):
        """misnamed function to execute the server listen loop"""
        self.runListenLoop()

    def runListenLoop(self):
        '''main loop used to accept sockets'''

        assert not self._started, "can't restart a SimpleServer"

        self.bindListener()

        #TODO design ronen: Should this be an atomic test-and-set operation?
        listenerSocket = self._listener

        while not self.shouldStop():
            try:
                self._started = True

                r, w, e = select.select([listenerSocket], [], [], .25)

                if len(r):
                    clientSocket, address = listenerSocket.accept()
                    try:
                        if self._nodelay:
                            clientSocket.setsockopt(
                                    socket.SOL_TCP,
                                    socket.TCP_NODELAY,
                                    0
                                    )
                            if sys.platform == "darwin":
                                clientSocket.setblocking(1)
                        self._onConnect(clientSocket, address)
                    except socket.error as e:
                        logging.error('socket error in listener loop!\n%s', traceback.format_exc(e))
            except Exception as e:
                if self.shouldStop():
                    return

                logging.warn("error in start loop\n" + traceback.format_exc(e))
                self.stop()
                sys.exit(1)


    def _onConnect(self, clientSocket, address):
        '''implemented by the subclass'''
        raise AttributeError()

    @staticmethod
    def connect(address, port, prebind=None, nodelay=True):
        '''connects to a two socket server and returns the down and up sockets

        if nodelay is true, then disable the nagle algorithm and send packets
            immediately.
        '''
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if prebind is not None:
                if callable(prebind):
                    bound = False
                    while not bound:
                        try:
                            s.bind(prebind())
                            bound = True
                        except socket.error as e:
                            print e
                            time.sleep(1)
                else:
                    s.bind(prebind)

            s.connect((address, port))

            if nodelay:
                s.setsockopt(
                        socket.SOL_TCP,
                        socket.TCP_NODELAY,
                        0
                        )
                if sys.platform == "darwin":
                    s.setblocking(1)
            return s
        except Exception as e:
            raise
            if isinstance(e, socket.error):
                raise socket_util.SocketException('error connecting to %s:%s' % (address, port))
            raise e

