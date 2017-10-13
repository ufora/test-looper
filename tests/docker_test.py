import unittest
import os
import test_looper
import test_looper.core.tools.Docker as Docker
import docker
import time

test_dir = os.path.join(os.path.split(os.path.split(test_looper.__file__)[0])[0], "tests")

dockerfile = """
FROM ufora/build:19b4ea8ffab4050b2db9d96262a976e1

RUN pip install docker
"""

class DockerTests(unittest.TestCase):
    def test_prune(self):
        Docker.DockerImage.removeDanglingDockerImages()

    def test_subdocker(self):
    	image = Docker.DockerImage.from_dockerfile_as_string(None, dockerfile, create_missing=True)
    	
    	container, watcher = image.runWithWatcher([
	    		"python", 
	    		"-c", 
	    		"import docker; i=docker.from_env().images.get('ubuntu:16.04');"
	    		"print docker.from_env().containers.run(i,['sleep','60'], detach=True)"
	    		]
    		)
    	
    	container.wait()

    	time.sleep(2.0)

    	self.assertTrue(len(watcher.containers_booted) == 1)

    	for c in watcher.containers_booted:
    		self.assertEqual(c.status, "running")
    		c.remove(force=True)

    
