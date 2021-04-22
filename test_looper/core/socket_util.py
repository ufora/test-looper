import struct
import errno
import socket
import logging
import ssl


class SocketException(Exception):
    def __init__(self, message):
        Exception.__init__(self, message)


class SocketClosed(Exception):
    def __init__(self, message):
        Exception.__init__(self, message)


sizeType = "<L"
longLength = struct.calcsize(sizeType)


def longToString(l):
    return struct.pack(sizeType, l)


def stringToLong(l):
    return struct.unpack(sizeType, l)[0]


def readBytes(sock, byteCount):
    assert byteCount < 1024 * 1024 * 1024

    tr = []
    if byteCount < 1:
        logging.warn("calling readBytes with a byteCount of %s", byteCount)
    while byteCount > 0:
        dat = sock.recv(byteCount)
        if dat == "":
            raise SocketException("Socket Disconnected")
        byteCount -= len(dat)
        tr.append(dat)
    tr = b"".join(tr)
    return tr


def readLong(sock):
    return stringToLong(readBytes(sock, longLength))


def writeLong(sock, l):
    sock.send(longToString(l))


def readString(sock):
    l = readLong(sock)
    if l:
        return readBytes(sock, l)
    else:
        return b""


def writeString(sock, s):
    sock.send(prependSize(s))


# Note: even if select has declared a socket "writeable" it can eventually
# block if sendall is used. So to achieve true non-blocking behavior we need
# to manage this ourselves.


class SocketReaderException(Exception):
    def __init__(self, message):
        Exception(self, message)


class SocketSenderException(Exception):
    def __init__(self, message):
        Exception(self, message)


def prependSize(s):
    return struct.pack(sizeType, len(s)) + s


def stripSize(s):
    size = struct.unpack(sizeType, s[:longLength])[0]
    return s[longLength : longLength + size], s[longLength + size :]


def sendFromBufferNonblock(sock, buff):
    while len(buff):
        dat = buff.pop(0)
        remaining = _sendDataNonblock(sock, dat)
        if len(remaining):
            buff.insert(0, remaining)
            return len(buff) > 0
    return len(buff) == 0


def _sendDataNonblock(sock, s):
    sent = 0
    while len(s) > sent:
        try:
            sent += sock.send(s[sent:])
        except socket.error as e:
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return s[sent:]
        except ssl.SSLError as e:
            if e.errno == ssl.SSL_ERROR_WANT_WRITE:
                # not tested, but this seems right
                return s[sent:]
            raise e
    return b""


def readIntoBufferNonblock(sock, buff):
    read = 0
    while True:
        try:
            msg = sock.recv(4096)
            if len(msg):
                read += len(msg)
                buff.append(msg)
            else:
                raise SocketClosed("socket was closed remotely")
        except ssl.SSLError as e:
            if e.errno == ssl.SSL_ERROR_WANT_READ:
                return read
            raise e
        except socket.error as e:
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return read
            raise e
