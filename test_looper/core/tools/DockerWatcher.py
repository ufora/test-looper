import docker
import threading
import socketserver
import socket
import tempfile
import os
import re
import select
import traceback
import logging
import json
import uuid
import time

docker_client = docker.from_env()
# initialize the docker threadpool
try:
    docker_client.containers.list()
except:
    pass


class HTTPRequestBuffer:
    def __init__(self):
        self.buf = b""

    def write(self, msg):
        self.buf += msg

    def consume_bytes(self, bytes):
        if len(self.buf) >= bytes:
            res = self.buf[:bytes]
            self.buf = self.buf[bytes:]
            return res
        else:
            return None

    def popHttpRequest(self):
        ix = self.buf.find(b"\r\n\r\n")
        if ix >= 0:
            header_lines = self.buf[:ix].split(b"\r\n")

            if header_lines[-1].startswith(b"Content-Length: "):
                length = int(header_lines[-1][len(b"Content-Length: ") :])

                if length + ix + 4 <= len(self.buf):
                    self.buf = self.buf[ix + 4 :]
                    data = self.consume_bytes(length)
                    assert data is not None

                    return b"\r\n".join(header_lines[:-1]), data
            else:
                self.buf = self.buf[ix + 4 :]
                return b"\r\n".join(header_lines), b""

    def popHttpResponse(self):
        ix = self.buf.find(b"\r\n\r\n")
        if ix >= 0:
            header_lines = self.buf[:ix].split(b"\r\n")

            if header_lines[-1].startswith(b"Content-Length: "):
                length = int(header_lines[-1][len(b"Content-Length: ") :])

                if length + ix + 4 <= len(self.buf):
                    self.buf = self.buf[ix + 4 :]
                    data = self.consume_bytes(length)
                    assert data is not None

                    return b"\r\n".join(header_lines) + b"\r\n\r\n" + data

            elif header_lines[-1].startswith(b"Transfer-Encoding: chunked"):
                data = self.consumeChunkedTransferEncoding(ix + 4)
                if data is not None:
                    return data
            else:
                self.buf = self.buf[ix + 4 :]
                return b"\r\n".join(header_lines) + b"\r\n\r\n"

    def chunk_line_at(self, ix):
        next_ix = self.buf.find(b"\r\n", ix)
        if next_ix >= 0:
            return self.buf[ix : next_ix + 2]

    def consumeChunkedTransferEncoding(self, index):
        if len(self.buf) < index + 4:
            return None

        while True:
            chunk_line = self.chunk_line_at(index)

            if chunk_line is None:
                return None

            if chunk_line[0] == b"0":
                length = 0
            else:
                length = int(chunk_line[:4], 16)

            index += len(chunk_line)

            if length == 0:
                index += 2
                if index > len(self.buf):
                    return None

                data = self.buf[:index]
                self.buf = self.buf[index:]
                return data

            if len(self.buf) < index + length + 2:
                return None

            index += length + 2

    @staticmethod
    def is_upgrade(msg):
        headers = msg.split(b"\n")
        for h in headers:
            if h == "":
                return False
            if h.strip().upper() == b"Connection: Upgrade".upper():
                return True
        return False


class DockerSocketRequestHandler(socketserver.BaseRequestHandler):
    def __init__(self, socket_thread, stop, *args):
        self.socket_thread = socket_thread
        self.stop = stop

        socketserver.BaseRequestHandler.__init__(self, *args)

    def bidirectional(self, sock):
        # just pass data back and forth without inspecting it
        while True:
            readers, writers, closed = select.select(
                [self.request, sock], [], [self.request, sock], 0.1
            )

            if self.request in readers:
                data = self.request.recv(512)

                if not data:
                    return

                sock.sendall(requestBuf.buf)

            if sock in readers:
                data = sock.recv(512)
                if not data:
                    return

                self.request.sendall(data)

    def handle(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect("/var/run/docker.sock")

        requestBuf = HTTPRequestBuffer()
        responseBuf = HTTPRequestBuffer()

        response_handlers = []

        try:
            while not self.stop[0]:
                readers, writers, closed = select.select(
                    [self.request, sock], [], [self.request, sock], 0.1
                )

                if self.request in readers:
                    data = self.request.recv(64)

                    requestBuf.write(data)

                    shouldBail = len(data) == 0

                    while True:
                        header_and_data = requestBuf.popHttpRequest()

                        if header_and_data:
                            header, data, on_response_message = self.modify_msg(
                                header_and_data[0], header_and_data[1]
                            )

                            assert isinstance(header, bytes)

                            if data:
                                assert isinstance(data, bytes)

                                out_msg = (
                                    header
                                    + (
                                        "\r\nContent-Length: %s\r\n\r\n" % len(data)
                                    ).encode("ascii")
                                    + data
                                )
                            else:
                                out_msg = header + b"\r\n\r\n"

                            sock.sendall(out_msg)

                            response_handlers.append(on_response_message)

                            if HTTPRequestBuffer.is_upgrade(header):
                                sock.sendall(requestBuf.buf)
                                self.request.sendall(responseBuf.buf)

                                return self.bidirectional(sock)
                        else:
                            break

                    if shouldBail:
                        return

                if sock in readers:
                    data = sock.recv(64)

                    responseBuf.write(data)

                    while True:
                        response = responseBuf.popHttpResponse()

                        if response:
                            on_message = response_handlers.pop(0)
                            on_message(response)

                            self.request.sendall(response)

                        else:
                            break

                    if not data:
                        return

        except:
            logging.error(
                "DockerWatcher failed in read loop:\n%s", traceback.format_exc()
            )
        finally:
            sock.close()

    def modify_msg(self, header, data):
        assert isinstance(header, bytes)
        assert isinstance(data, bytes)

        lines = header.split(b"\r\n")

        result = re.match(
            b"POST /[^/]+/containers/([0-9a-f]+)/start.*", lines[0].strip()
        )
        if result:
            containerID = result.group(1).decode("ASCII")
            self.socket_thread.watcher.new_container(
                self.socket_thread.containerID, containerID
            )
            return header, data, lambda msg: None

        result = re.match(
            b"POST /([^/]+)/containers/create(|\\?name=/?[a-zA-Z0-9_-]+) (.*)",
            lines[0].strip(),
        )
        if result:
            api = result.group(1).decode("ascii")
            name = result.group(2).decode("ascii")
            post = result.group(3).decode("ascii")
            if name != "":
                name = name[6:]
            else:
                name = None

            data_json = json.loads(data)
            if name is not None:
                data_json["Name"] = name

            onContainerIDKnown = self.socket_thread.watcher.processCreate(
                self.socket_thread.containerID, data_json
            )

            if "Name" in data_json:
                name = data_json["Name"]
                del data_json["Name"]
            else:
                name = None

            if name is not None:
                lines[0] = "POST /{api}/containers/create?name={name} {post}".format(
                    api=api, name=name, post=post
                )
            else:
                lines[0] = "POST /{api}/containers/create {post}".format(
                    api=api, post=post
                )

            def onResponseMessage(msg):
                responseLines = msg.split(b"\r\n")
                ix = responseLines.index(b"")

                try:
                    jsonObj = json.loads(b"\r\n".join(responseLines[ix + 1 :]))
                except:
                    raise Exception(
                        "Couldn't parse:\n"
                        + b"\r\n".join(responseLines).decode("ascii")
                        + "\nresponse to "
                        + str(lines)
                    )

                if "Id" in jsonObj:
                    onContainerIDKnown(jsonObj["Id"])
                else:
                    logging.critical("Didn't understand response: %s", jsonObj)

            return (
                b"\r\n".join(
                    [x.encode("ASCII") if isinstance(x, str) else x for x in lines]
                ),
                json.dumps(data_json).encode("ASCII"),
                onResponseMessage,
            )

        return header, data, lambda msg: None


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    pass


class DockerSocket:
    def __init__(self, watcher):
        self.containerID = None
        self.watcher = watcher

        self.socket_dir = tempfile.mkdtemp()
        self.socket_name = os.path.join(self.socket_dir, "docker.sock")

        self.stop = [False]

        self.server = Server(
            self.socket_name,
            lambda *args: DockerSocketRequestHandler(self, self.stop, *args),
        )
        self.server.daemon_threads = True

        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.1)
        )
        self.thread.daemon = True
        self.thread.start()

    def shutdown(self):
        self.stop[0] = True

        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class DockerWatcher:
    def __init__(self, name_prefix="test_looper_"):
        self._containers_booted = []

        self.serverthreads = []

        self.target_network = docker_client.networks.create(
            name_prefix + "_" + str(uuid.uuid4()), driver="bridge"
        )

        self.mappedVolumesByParentID = {}

        self.name_prefix = name_prefix

        self._lock = threading.RLock()

    def newSocketThread(self):
        with self._lock:
            self.serverthreads.append(DockerSocket(self))
            return self.serverthreads[-1]

    def __enter__(self, *args):
        return self

    def __exit__(self, *args):
        self.shutdown()

    def new_container(self, parentContainer, containerID):
        with self._lock:
            self._containers_booted.append(containerID)

    def processCreate(self, parentContainerId, createJson):
        with self._lock:
            unmangled_name = None
            if self.name_prefix is not None:
                if "Name" not in createJson:
                    createJson["Name"] = "container_" + str(uuid.uuid4())

                unmangled_name = createJson["Name"]
                createJson["Name"] = self.mangleName_(createJson["Name"])

            # create the new thread here and map volumes, but force the
            # caller to set the containerID for us
            newThread = self.newSocketThread()

            existing_volumes = self.mappedVolumesByParentID[parentContainerId]

            new_binds = [newThread.socket_dir + ":/var/run:rw"]
            if "Volumes" not in createJson:
                createJson["Volumes"] = {}
            createJson["Volumes"]["/var/run"] = {}

            existing_binds = createJson["HostConfig"].get("Binds", [])

            for bind in existing_binds:
                new_binds.append(self.update_bind(existing_volumes, bind))

            createJson["HostConfig"]["Binds"] = new_binds

            if "LogConfig" not in createJson["HostConfig"]:
                createJson["HostConfig"]["LogConfig"] = {
                    "Type": "json-file",
                    "Config": {},
                }

            def onContainerIDKnown(containerID):
                logging.info(
                    "Container %s creating container %s", parentContainerId, containerID
                )
                new_binds_dict = {}
                for b in new_binds:
                    # note we're deliberately leaking the r/w flag, which means
                    # a child container could "un-read-only" a mount right now.
                    # not something to worry about yet.

                    k, v, _ = b.split(":")
                    new_binds_dict[k] = v

                self.mappedVolumesByParentID[containerID] = new_binds_dict

                try:
                    self.target_network.connect(
                        containerID, aliases=[unmangled_name] if unmangled_name else []
                    )
                except:
                    logging.error(
                        "FAILED connecting container %s to network %s:\n\n%s",
                        containerID,
                        self.target_network,
                        traceback.format_exc(),
                    )

            return onContainerIDKnown

    def update_bind(self, existing_volumes, bind):
        host, container, rw = bind.split(":")

        for existing_host, existing_container in existing_volumes.items():
            existing_container = existing_container["bind"]

            if existing_container == host:
                return existing_host + ":" + container + ":" + rw
            if host.startswith(existing_container + "/"):
                return (
                    existing_host
                    + "/"
                    + host[len(existing_container) + 1 :]
                    + ":"
                    + container
                    + ":"
                    + rw
                )

        assert False, "Can't create! no way to map binding %s in %s" % (bind, existing)

    def mangleName_(self, name):
        if not isinstance(name, str):
            name = name.decode("ASCII")

        if self.name_prefix is None:
            return name

        if name[:1] == "/":
            if self.name_prefix[1:] == "/":
                return self.name_prefix + "_" + name[1:]
            else:
                return "/" + self.name_prefix + name[1:]
        else:
            return self.name_prefix + name

    @property
    def containers_booted(self):
        res = []
        for c in self._containers_booted:
            try:
                res.append(docker_client.containers.get(c))
            except:
                logging.exception("We booted container %s but can't find it now." % c)

        return res

    def shutdown(self):
        for c in self.containers_booted:
            logging.info("DockerWatcher removing container %s", c)
            c.remove(force=True)

        self.target_network.remove()

        for t in self.serverthreads:
            t.shutdown()

    def run(self, image, args, start=True, **kwargs):
        with self._lock:
            kwargs = dict(kwargs)

            if "volumes" in kwargs:
                volumes = kwargs["volumes"]
                del kwargs["volumes"]
            else:
                volumes = {}

            orig_volumes = dict(volumes)

            # some old code just passes the bind argument directly
            for k in orig_volumes:
                if isinstance(orig_volumes[k], str):
                    orig_volumes[k] = {"bind": orig_volumes[k]}

            volumes = dict(orig_volumes)

            if "name" not in kwargs:
                kwargs["name"] = "uuid_" + str(uuid.uuid4()).replace("-", "")

            unmangled_name = kwargs["name"]
            kwargs["name"] = self.mangleName_(kwargs["name"])

            image = docker_client.images.get(image.image)

            sockThread = self.newSocketThread()

            volumes[sockThread.socket_dir] = {"bind": "/var/run"}

            container = docker_client.containers.create(
                image, args, volumes=volumes, **kwargs
            )

            self.mappedVolumesByParentID[container.id] = orig_volumes

            sockThread.containerID = container.id

            self.target_network.connect(
                container, aliases=[unmangled_name] if unmangled_name else []
            )

            if start:
                container.start()

            self._containers_booted.append(container.id)

            return container
