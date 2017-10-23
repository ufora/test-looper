import docker
import threading
import SocketServer
import socket
import httplib
import tempfile
import os
import re
import select
import traceback
import logging
import simplejson


class SocketWrapper:
    def __init__(self, sock):
        self.sock = sock
        self.buf = ""

    @staticmethod
    def is_upgrade(msg):
        headers = msg.split("\n")
        for h in headers:
            if h == "":
                return False
            if h.strip().upper() == "Connection: Upgrade".upper():
                return True
        return False

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
                return None, None
            
            lines.append(l)
            if len(lines) > 1 and lines[-1] == "\r\n":
                return "".join(lines), ""

            if lines[-1].startswith("Content-Length: "):
                length = int(lines[-1][len("Content-Length: "):])
                lines.pop()

                sep = self.readline()
                if sep is None:
                    return None, None

                if length:
                    data = self.read(length)
                    assert len(data) == length

                    return "".join(lines), data

                return "".join(lines), ""

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
    def __init__(self, watcher, *args):
        self.watcher = watcher

        SocketServer.BaseRequestHandler.__init__(self, *args)

    def handle(self):
        try:
            header, data = SocketWrapper(self.request).read_http_request()

            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect("/var/run/docker.sock")

            header, data = self.modify_msg(header, data)

            out_msg = header + "Content-Length: %s\r\n\r\n" % len(data) + data

            sock.sendall(out_msg)

            s = SocketWrapper(sock)

            if SocketWrapper.is_upgrade(header):
                res = s.readHttpResponse()
                self.request.sendall(res)
                
                while True:
                    readers,writers,closed = select.select([self.request, sock], [], [self.request, sock])

                    if self.request in readers:
                        data = self.request.recv(1024)
                        sock.sendall(data)
                    if sock in readers:
                        data = sock.recv(1024)
                        if not data:
                            break
                        self.request.sendall(data)
            else:
                res = s.readHttpResponse()
                self.request.sendall(res)
        except:
            logging.error("DockerWatcher failed in read loop:\n%s", traceback.format_exc())
            print traceback.format_exc()
        finally:
            sock.close()

    def modify_msg(self, header, data):
        lines = header.split("\r\n")

        result = re.match("POST /[^/]+/containers/([0-9a-f]+)/start.*", lines[0].strip())
        if result:
            containerID = result.group(1)
            self.watcher._containers_booted.append(containerID)
            return header, data

        result = re.match("POST /([^/]+)/containers/create(|\?name=/?[a-zA-Z0-9_-]+) (.*)", lines[0].strip())
        if result:
            api = result.group(1)
            name = result.group(2)
            post = result.group(3)
            if name != "":
                name = name[6:]
            else:
                name = None

            data_json = simplejson.loads(data)
            if name is not None:
                data_json["Name"] = name

            self.watcher.processCreate(data_json)

            if "Name" in data_json:
                name = data_json["Name"]
                del data_json["Name"]
            else:
                name = None

            if name is not None:
                lines[0] = "POST /{api}/containers/create?name={name} {post}".format(api=api,name=name,post=post)
            else:
                lines[0] = "POST /{api}/containers/create  {post}".format(api=api,post=post)

            return "\r\n".join(lines), simplejson.dumps(data_json)

        return header, data

class Server(SocketServer.ThreadingMixIn, SocketServer.UnixStreamServer):
    pass

class DockerWatcher:
    def __init__(self):
        self.socket_dir = tempfile.mkdtemp()
        self.socket_name = os.path.join(self.socket_dir, "docker.sock")
        self._containers_booted = []

        self.server = Server(self.socket_name, lambda *args: DockerSocketRequestHandler(self, *args))
        self.server.daemon_threads = True

        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.1))
        self.thread.daemon=True
        self.thread.start()

        self.target_network = None
        self.name_prefix = None

    def processCreate(self, createJson):
        if self.name_prefix is not None:
            if "Name" in createJson:
                if createJson["Name"][:1] == "/":
                    if self.name_prefix == "/":
                        createJson["Name"] = self.name_prefix + "_" + createJson["Name"][:1]
                    else:
                        createJson["Name"] = "/" + self.name_prefix + createJson["Name"][:1]
                else:
                    createJson["Name"] = self.name_prefix + createJson["Name"]

        if self.target_network is not None:
            createJson["HostConfig"]["NetworkMode"] = self.target_network

    @property
    def containers_booted(self):
        return [docker.from_env().containers.get(c) for c in self._containers_booted]

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


    def run(self, image, args, **kwargs):
        kwargs = dict(kwargs)
        if 'volumes' in kwargs:
            volumes = kwargs['volumes']
            del kwargs['volumes']
        else:
            volumes = {}

        client = docker.from_env()
        image = client.images.get(image.image)

        volumes[self.socket_dir] = "/var/run"

        container = client.containers.run(image, args, volumes=volumes, detach=True, **kwargs)

        return container

