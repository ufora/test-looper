import boto
import threading
import os
import cherrypy
import subprocess
import logging
import traceback

import test_looper.core.TimerQueue as TimerQueue
timerQueue = TimerQueue.TimerQueue(16)

class AwsArtifactStorage(object):
    def __init__(self, ec2_config, aws_region):
        self.test_result_bucket_name = ec2_config['test_result_bucket']
        self.builds_bucket_name = ec2_config['builds_bucket']
        self.aws_region = ec2_config['aws_region']

    def testResultKeysFor(self, testId):
        keys = list(self.get_test_result_bucket().list(prefix=testId))

        result = []

        for k in keys:
            prefix = testId + '/'
            assert k.name.startswith(prefix)
            result.append(k.name[len(prefix):])

        logging.info("result: %s", result)

        return result

    def testContentsHtml(self, testId, key):        
        bucket = self.get_test_result_bucket()

        keys = list(bucket.list(prefix=testId + "/" + key))

        logging.info("Prefix = %s. keys = %s. key = %s", testId, keys, key)

        redirect = keys[0].generate_url(expires_in=300)

        raise cherrypy.HTTPRedirect(redirect)

    @property
    def connection(self):
        return boto.s3.connect_to_region(self.aws_region)

    def uploadTestArtifacts(self, testId, machineId, testOutputDir):
        bucket = self.get_test_result_bucket()

        def uploadFile(path, semaphore):
            try:
                logging.info("Uploading %s", path)
                headers = {}
                if '.log' in path:
                    headers['Content-Type'] = 'text/plain'
                elif '.xml' in path:
                    headers['Content-Type'] = 'text/xml'

                if path.endswith('.gz'):
                    headers['Content-Encoding'] = 'gzip'
                key = bucket.new_key(testId + "/" + machineId + '/' + os.path.split(path)[-1])
                key.set_contents_from_filename(path, headers=headers)
            except:
                logging.error("Failed to upload %s:\n%s", path, traceback.format_exc())
            finally:
                semaphore.release()

        for logFile in os.listdir(testOutputDir):
            if logFile.endswith(('.log', '.out')):
                logFile = os.path.join(testOutputDir, logFile)
                subprocess.call(['gzip %s' % logFile], shell=True)


        sem = threading.Semaphore(0)
        for logFile in os.listdir(testOutputDir):
            logFile = os.path.join(testOutputDir, logFile)
            timerQueue.enqueueWorkItem(uploadFile, (logFile, sem))

        for logFile in os.listdir(testOutputDir):
            sem.acquire()

    def get_test_result_bucket(self):
        return self.connection.get_bucket(self.test_result_bucket_name)

    def get_build_bucket(self):
        return self.connection.get_bucket(self.builds_bucket_name)

    def get_build_s3_url(self, key_name):
        return "s3://%s/%s" % (self.builds_bucket_name, key_name)

    def upload_build(self, key_name, file_name):
        logging.info("Uploading build '%s' to %s", file_name,key_name)

        key = boto.s3.key.Key(self.get_build_bucket(), key_name)
        key.set_contents_from_filename(file_name)

    def download_build(self, key_name, dest):
        key = boto.s3.key.Key(self.get_build_bucket(), key_name)
        key.get_contents_to_filename(dest)

    def build_exists(self, key_name):
        return self.get_build_bucket().get_key(key_name) is not None

class LocalArtifactStorage(object):
    def __init__(self, config):
        self.build_storage_path = os.path.expandvars(config["build_storage_path"])
        self.test_artifacts_storage_path = os.path.expandvars(config["test_artifacts_storage_path"])

    def testContents(self, testId, key):  
        with open(os.path.join(self.test_artifacts_storage_path, testId, key), "r") as f:
            return f.read()

    def testContentsHtml(self, testId, key):  
        if key.endswith(".log.gz"):
            cherrypy.response.headers['Content-Type'] = 'text/plain'
            cherrypy.response.headers['Content-Encoding'] = 'gzip'
            cherrypy.response.headers["Content-Disposition"] = "filename=\"" + key[:-3] + "\";"
        else:
            cherrypy.response.headers['Content-Type'] = 'application/octet-stream'
            cherrypy.response.headers["Content-Disposition"] = "attachment; filename=\"" + key + "\";"


        return self.testContents(testId, key)
    
    def filecopy(self, dest_path, src_path):
        dirname = os.path.split(dest_path)[0]
        try:
            os.makedirs(dirname)
        except OSError:
            pass
        
        with open(dest_path, "w") as target:
            with open(src_path, "r") as src:
                while True:
                    data = src.read(1024 * 1024)
                    if data:
                        target.write(data)
                    else:
                        break

    def testResultKeysFor(self, testId):
        path = os.path.join(self.test_artifacts_storage_path, testId)
        
        if not os.path.exists(path):
            return []

        return os.listdir(path)

    def upload_build(self, key_name, file_name):
        tgt = os.path.join(self.build_storage_path, key_name)
        self.filecopy(tgt, file_name)

    def download_build(self, key_name, dest):
        self.filecopy(dest, os.path.join(self.build_storage_path, key_name))

    def uploadTestArtifacts(self, testId, machineId, testOutputDir):
        try:
            os.makedirs(os.path.join(self.test_artifacts_storage_path, testId))
        except OSError:
            pass

        def uploadFile(path, semaphore):
            try:
                logging.info("Uploading %s", path)
                target_path = testId + "/" + machineId + '-' + os.path.split(path)[-1]

                self.filecopy(
                    os.path.join(self.test_artifacts_storage_path, target_path),
                    path
                    )
            except:
                logging.error("Failed to upload %s:\n%s", path, traceback.format_exc())
            finally:
                semaphore.release()

        for logFile in os.listdir(testOutputDir):
            if logFile.endswith(('.log', '.out')):
                logFile = os.path.join(testOutputDir, logFile)
                subprocess.call(['gzip %s' % logFile], shell=True)

        sem = threading.Semaphore(0)
        for logFile in os.listdir(testOutputDir):
            logFile = os.path.join(testOutputDir, logFile)
            timerQueue.enqueueWorkItem(uploadFile, (logFile, sem))

        for logFile in os.listdir(testOutputDir):
            sem.acquire()


    def build_exists(self, key_name):
        return os.path.exists(os.path.join(self.build_storage_path, key_name))

def storageFromConfig(config):
    if config['type'] == 's3':
        return AwsArtifactStorage(config)
    elif config['type'] == 'local_disk':
        return LocalArtifactStorage(config)
    else:
        raise Exception("Invalid artifact storage type. Pick 's3' or 'local_disk'.")