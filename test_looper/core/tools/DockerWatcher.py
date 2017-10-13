import docker
import threading
import SocketServer
import socket
import httplib
import tempfile
import os
import re

class SocketWrapper:
    def __init__(self, sock):
        self.sock = sock
        self.buf = ""

    def readline(self):
        while True:
            if "\r\n" in self.buf:
                ix = self.buf.find("\r\n")
                line = self.buf[:ix+2]
                self.buf = self.buf[ix+2:]
                return line

            data = self.sock.recv(1024)
            if not data:
                return None

            self.buf += data

    def read(self, bytes):
        while len(self.buf) < bytes:
            data = self.sock.recv(1023)
            if not data:
                return None

            self.buf += data
        res = self.buf[:bytes]
        self.buf = self.buf[bytes:]
        return res

    def read_http_request(self):
        lines = []

        while True:
            l = self.readline()
            if not l:
                return None
            lines.append(l)
            if len(lines) > 1 and lines[-1] == "\r\n":
                return "".join(lines)

            if lines[-1].startswith("Content-Length: "):
                length = int(lines[-1][len("Content-Length: "):])

                lines.append(self.readline())
                if lines[-1] is None:
                    return None

                if length:
                    lines.append(self.read(length))
                    if lines[-1] is None:
                        return None
                return "".join(lines)

    def readChunkedTransferEncoding(self):
        lines = []
        while True:
            header = self.readline()
            if header is None:
                return None
            lines.append(header)

            if header.find(";") >= 0:
                header = header[:header.find(";")]

            headerlen = int(header.strip(),16)

            if headerlen == 0:
                return lines

            lines.append(self.read(headerlen+2))

            if lines[-1] is None:
                return None

    def readHttpResponse(self):
        lines = []
        while True:
            lines.append(self.readline())
            if lines[-1] is None:
                sock.close()
                return

            if lines[-1] == "Transfer-Encoding: chunked\r\n":
                lines.append(self.readline())
                if lines[-1] is None:
                    return
                
                res = self.readChunkedTransferEncoding()
                if res is None:
                    return
                lines.extend(res)
                break
            
            if lines[-1].startswith("Content-Length: "):
                length = int(lines[-1][len("Content-Length: "):])
                
                lines.append(self.readline())
                if lines[-1] is None:
                    return

                if length:
                    lines.append(self.read(length))
                break

            if lines[-1] == "\r\n":
                break

        return "".join(lines)                   

class DockerSocketRequestHandler(SocketServer.BaseRequestHandler):
    def __init__(self, containers_booted, *args):
        self.containers_booted = containers_booted

        SocketServer.BaseRequestHandler.__init__(self, *args)


    def handle(self):
        msg = SocketWrapper(self.request).read_http_request()
        
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect("/var/run/docker.sock")

        sock.sendall(self.modify_msg(msg))

        s = SocketWrapper(sock)

        res = s.readHttpResponse()
        self.request.sendall(res)

        sock.close()

    def modify_msg(self, msg):
        lines = msg.split("\r\n")
        
        result = re.match("POST /[^/]+/containers/([0-9a-f]+)/start.*", lines[0].strip())
        if result:
            containerID = result.group(1)
            self.containers_booted.append(containerID)
        return msg

class Server(SocketServer.ThreadingMixIn, SocketServer.UnixStreamServer):
    pass

class DockerWatcher:
    def __init__(self):
        self.socket_dir = tempfile.mkdtemp()
        self.socket_name = os.path.join(self.socket_dir, "docker.sock")
        self._containers_booted = []

        self.server = Server(self.socket_name, lambda *args: DockerSocketRequestHandler(self._containers_booted, *args))
        self.server.daemon_threads = True

        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.1))
        self.thread.daemon=True
        self.thread.start()

    @property
    def containers_booted(self):
        return [docker.from_env().containers.get(c) for c in self._containers_booted]
    
    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


