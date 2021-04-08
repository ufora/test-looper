import unittest
import os
import test_looper
import test_looper.core.tools.Docker as Docker
import test_looper.core.tools.DockerWatcher as DockerWatcher
import docker
import time
import uuid

test_dir = os.path.join(os.path.split(os.path.split(test_looper.__file__)[0])[0], "test_looper_tests")

dockerfile = """
FROM ufora/build:19b4ea8ffab4050b2db9d96262a976e1

RUN pip install docker==2.5.0
"""
docker_client = docker.from_env()
docker_client.containers.list()

class DockerTests(unittest.TestCase):
    def test_prune(self):
        Docker.DockerImage.removeDanglingDockerImages()

    def test_subdocker(self):
        image = Docker.DockerImage.from_dockerfile_as_string(None, dockerfile, create_missing=True)
        
        containers = docker_client.containers.list(all=True)

        with DockerWatcher.DockerWatcher() as watcher:
            container = watcher.run(
                image, 
                ["python", 
                    "-c", 
                    "import docker; i=docker.from_env().images.get('ubuntu:16.04');"
                    "print(docker.from_env().containers.run(i,['sleep','60'], detach=True))"]
                )
            
            container.wait()

            self.assertEqual(len(watcher.containers_booted), 2, container.logs())

            self.assertEqual(
                sorted([c.status for c in watcher.containers_booted]), 
                ["exited", "running"]
                )

        containers2 = docker_client.containers.list(all=True)

        self.assertEqual(len(containers), len(containers2))

    def test_docker_killall(self):
        image = Docker.DockerImage.from_dockerfile_as_string(None, dockerfile, create_missing=True)
        
        containers = docker_client.containers.list(all=True)

        name_prefix = "docker_test_" + str(uuid.uuid4())

        with DockerWatcher.DockerWatcher(name_prefix) as watcher:
            container = watcher.run(
                image, 
                ["python", 
                    "-c", 
                    "import docker; i=docker.from_env().images.get('ubuntu:16.04');"
                    "print('booting ', docker.from_env().containers.run(i,['sleep','60'], detach=True).name)"]
                )

            print("booted container ", container.name)
            
            container.wait()

            self.assertTrue(Docker.killAllWithNamePrefix(name_prefix) == 1, container.logs())

    def test_subdocker_boots_into_own_network(self):
        image = Docker.DockerImage.from_dockerfile_as_string(None, dockerfile, create_missing=True)
        
        containers = docker_client.containers.list(all=True)

        with DockerWatcher.DockerWatcher("namespace_1") as watcher1:
            with DockerWatcher.DockerWatcher("namespace_2") as watcher2:
                listener1 = watcher1.run(
                    image, 
                    ["python", "/test_dir/test_projects/simple_project/socket_listener.py", "8000", "listener1"],
                    volumes={test_dir:"/test_dir"},
                    name="listener"
                    )
                listener2 = watcher2.run(
                    image, 
                    ["python", "/test_dir/test_projects/simple_project/socket_listener.py", "8000", "listener2"],
                    volumes={test_dir:"/test_dir"}, 
                    name="listener"
                    )

                sender1 = watcher1.run(
                    image, 
                    ["python", "/test_dir/test_projects/simple_project/socket_sender.py", "listener", "8000", "listener1"],
                    volumes={test_dir:"/test_dir"},
                    name="sender"
                    )
                sender2 = watcher2.run(
                    image, 
                    ["python", "/test_dir/test_projects/simple_project/socket_sender.py", "listener", "8000", "listener2"],
                    volumes={test_dir:"/test_dir"},
                    name="sender"
                    )

                results = [
                    listener1.wait(),
                    listener2.wait(),
                    sender1.wait(),
                    sender2.wait()
                    ]

                expected = [{'Error': None, 'StatusCode': 0}] * 4

                self.assertEqual(
                    results, 
                    [{'Error': None, 'StatusCode': 0}] * 4, 
                    str(results) + " != " + str(expected) + "\n\n" + 
                    "\n".join(["*******\n" + x for x in [l.logs(stdout=True,stderr=True).decode("ASCII") for l in [listener1, listener2, sender1, sender2]]])
                    )

        containers2 = docker_client.containers.list(all=True)

        self.assertEqual(len(containers), len(containers2))
        
    def test_internally_booted_subdocker_network_isolation(self):
        image = Docker.DockerImage.from_dockerfile_as_string(None, dockerfile, create_missing=True)
        
        containers = docker_client.containers.list(all=True)

        with DockerWatcher.DockerWatcher("namespace_1") as watcher1:
            with DockerWatcher.DockerWatcher("namespace_2") as watcher2:
                listener1 = watcher1.run(
                    image, 
                    ["python", "/test_dir/test_projects/simple_project/socket_listener_in_new_container.py", image.image, "child", "8000", "listener1"],
                    volumes={test_dir:"/test_dir"},
                    name="listener"
                    )
                listener2 = watcher2.run(
                    image, 
                    ["python", "/test_dir/test_projects/simple_project/socket_listener_in_new_container.py", image.image, "child", "8000", "listener2"],
                    volumes={test_dir:"/test_dir"}, 
                    name="listener"
                    )

                time.sleep(2.0)
                
                sender1 = watcher1.run(
                    image, 
                    ["python", "/test_dir/test_projects/simple_project/socket_sender.py", "child", "8000", "listener1"],
                    volumes={test_dir:"/test_dir"},
                    name="sender"
                    )
                sender2 = watcher2.run(
                    image, 
                    ["python", "/test_dir/test_projects/simple_project/socket_sender.py", "child", "8000", "listener2"],
                    volumes={test_dir:"/test_dir"},
                    name="sender"
                    )

                results = [
                    listener1.wait(),
                    listener2.wait(),
                    sender1.wait(),
                    sender2.wait()
                    ]

                expected = [{'Error': None, 'StatusCode': 0}] * 4

                self.assertEqual(
                    results, 
                    expected, 
                    str(results) + " != " + str(expected) + "\n\n" + 
                    "\n".join(["*******\n" + x for x in [l.logs(stdout=True,stderr=True).decode('ASCII') for l in [
                        listener1, listener2, 
                        sender1, sender2
                        ]]])
                    )

        containers2 = docker_client.containers.list(all=True)

        self.assertEqual(len(containers), len(containers2))
        