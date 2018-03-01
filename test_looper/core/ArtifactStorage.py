import boto3
import botocore
import threading
import os
import logging
import traceback
import tempfile
import tarfile
import shutil
import gzip
import test_looper.core.algebraic as algebraic
import test_looper.core.TimerQueue as TimerQueue

timerQueue = TimerQueue.TimerQueue(16)

FileContents = algebraic.Alternative("FileContents")
FileContents.Inline = {
    "content_type": str,
    "content_encoding": str,
    "content_disposition": str,
    "content": str
    }
FileContents.Redirect = {"url": str}

Encoding = algebraic.Alternative("Encoding")

class ArtifactStorage(object):
    @staticmethod
    def keyname_to_encoding(key):
        if key.endswith(".out.gz") or key.endswith(".log.gz") or key.endswith(".txt.gz") or key.endswith(".stdout.gz") or key.endswith(".stderr.gz"):
            return ("text/plain", key[:-3], True)
        if key.endswith(".txt") or key.endswith(".log") or key.endswith(".out"):
            return ("text/plain", key, False)
        if key.endswith(".stdout") or key.endswith(".stderr"):
            return ("text/plain", key, False)
        return ("application/octet-stream", key, False)

    def sanitizeName(self, name):
        return name.replace("/", "_").replace("\\", "_").replace(":","_").replace(" ","_")

    def testResultKeysAndSizesForIndividualTest(self, repoName, commitHash, testId, testName):
        subPrefix = "individual_test_logs/" + self.sanitizeName(testName)

        res = []

        for key,sz in self.testResultKeysForWithSizes(repoName, commitHash, testId, subPrefix):
            res.append((subPrefix + "/" + key, sz))
        
        return res

    def uploadIndividualTestArtifacts(self, repoName, commitHash, testId, pathsToUpload):
        def uploadArtifact(testName, path, semaphore):
            try:
                testName = self.sanitizeName(testName)
                filename = os.path.basename(path)

                self.uploadSingleTestArtifact(
                    repoName, 
                    commitHash, 
                    testId, 
                    "individual_test_logs/" + testName + "/" + filename, 
                    path
                    )
            except:
                logging.error("Failed to upload %s:\n%s", path, traceback.format_exc())
            finally:
                semaphore.release()

        sem = threading.Semaphore(0)
        counts = 0

        for testName, paths in pathsToUpload.iteritems():
            for path in paths:
                timerQueue.enqueueWorkItem(uploadArtifact, (testName, path, sem))
                counts += 1

        for _ in xrange(counts):
            sem.acquire()

    def uploadTestArtifacts(self, reponame, commitHash, testId, testOutputDir, reserved_names):
        """Upload all the files in 'testOutputDir'.

        reserved_names - set of reserved filenames used to ensure we don't 
            conflict with special outputs reserved by the looper
        """
        all_paths = set(os.listdir(testOutputDir))

        def uploadFile(path, semaphore):
            try:
                full_path = os.path.join(testOutputDir, path)

                if os.path.isdir(full_path):
                    with tarfile.open(full_path + ".tar.gz", "w:gz") as tf:
                        tf.add(full_path, arcname=path)

                    path += ".tar.gz"
                    full_path += ".tar.gz"
                elif path.endswith(('.log', '.out', ".stdout", ".stderr")):
                    with gzip.open(full_path + ".gz", "wb") as gzip_f:
                        with open(full_path, "rb") as f:
                            shutil.copyfileobj(f, gzip_f)

                    full_path = full_path + ".gz"
                    path = path + ".gz"

                if path in reserved_names:
                    prefix = "0_"
                    while prefix + path in all_paths:
                        prefix = str(int(prefix[:-1])+1) + "_"
                    path = prefix + path

                self.uploadSingleTestArtifact(reponame, commitHash, testId, path, full_path)
            except:
                logging.error("Failed to upload %s:\n%s", path, traceback.format_exc())
            finally:
                semaphore.release()

        sem = threading.Semaphore(0)
        counts = 0
        for logFile in os.listdir(testOutputDir):
            timerQueue.enqueueWorkItem(uploadFile, (logFile, sem))
            counts += 1

        for _ in xrange(counts):
            sem.acquire()

    def testResultKeysFor(self, repoName, commitHash, testId):
        """Return a list of test results for a given testId.

        testId: str
        returns: list of strings with result keys
        """
        assert False, "Subclasses implement"

    def testContentsHtml(self, repoName, commitHash, testId, key):
        """Get a FileContents for a given testId and key"""
        assert False, "Subclasses implement"

    def uploadSingleTestArtifact(self, reponame, commitHash, testId, artifact_name, path):
        """Upload a single file as a test artifact for 'testId'"""
        assert False, "Subclasses implement"

    def buildContentsHtml(self, repoName, commitHash, key):
        """Get a FileContents for a given build key"""
        assert False, "Subclasses implement"

    def upload_build(self, repoName, commitHash, key_name, file_name):
        """Upload a build in 'file_name' to build key 'key_name'"""
        assert False, "Subclasses implement"

    def download_build(self, repoName, commitHash, key_name, dest):
        """Download a build in 'key_name' to 'dest'"""
        assert False, "Subclasses implement"

    def clear_build(self, repoName, commitHash, key_name):
        """Clear a build"""
        assert False, "Subclasses implement"

    def build_exists(self, repoName, commitHash, key_name):
        """Returns true if a build with 'key_name' exists. False otherwise."""
        assert False, "Subclasses implement"

class AwsArtifactStorage(ArtifactStorage):
    def __init__(self, config):
        ArtifactStorage.__init__(self)

        self.bucket_name = config.bucket
        self.region = config.region
        self.build_artifact_key_prefix = config.build_artifact_key_prefix
        self.test_artifact_key_prefix = config.test_artifact_key_prefix

    @property
    def _session(self):
        return boto3.Session(region_name=self.region)

    @property
    def _bucket(self):
        return self._session.resource('s3').Bucket(self.bucket_name)

    def testResultKeysForWithSizes(self, repoName, commitHash, testId, subPrefix=None):
        prefix = self.test_artifact_key_prefix + "/" + repoName + "/" + commitHash + "/" + testId + "/"

        if subPrefix:
            prefix = prefix + subPrefix + "/"

        keys = list(self._bucket.objects.filter(Prefix=prefix))

        result = []

        for k in keys:
            assert k.key.startswith(prefix)
            result.append((k.key[len(prefix):],k.size))

        return result

    
    def testResultKeysFor(self, repoName, commitHash, testId):
        return [x[0] for x in self.testResultKeysForWithSizes(repoName, commitHash, testId)]

    def uploadSingleTestArtifact(self, repoName, commitHash, testId, key, full_path):
        content_type, keyname, is_gzipped = ArtifactStorage.keyname_to_encoding(key)

        with open(full_path, "rb") as f:
            kwargs = {}
            if is_gzipped:
                kwargs["ContentEncoding"] = "gzip"

            if content_type != "text/plain":
                kwargs["ContentDisposition"]="attachment; filename=\"" + keyname + "\";"

            self._bucket.put_object(
                Body=f,
                ContentType=content_type,
                Key=self.test_artifact_key_prefix + "/" + repoName + "/" + commitHash + "/" + testId + "/" + key,
                **kwargs
                )

    def testContentsHtml(self, repoName, commitHash, testId, key):
        content_type, keyname, is_gzipped = ArtifactStorage.keyname_to_encoding(key)
            
        Params = {'Bucket': self.bucket_name, 'Key': self.test_artifact_key_prefix + "/" + repoName + "/" + commitHash + "/" + testId + "/" + key}
        if is_gzipped:
            Params["ResponseContentEncoding"] = "gzip"
        Params["ResponseContentType"] = content_type
        if content_type != "text/plain":
            Params["ResponseContentDisposition"] = "attachment; filename=\"" + keyname + "\";"
        else:
            Params["ResponseContentDisposition"] = "inline"

        return FileContents.Redirect(
            self._session.client('s3').generate_presigned_url(
                'get_object', 
                Params = Params, 
                ExpiresIn = 300
                )
            )

    def buildContentsHtml(self, repoName, commitHash, key):
        content_type, keyname, is_gzipped = ArtifactStorage.keyname_to_encoding(key)
            
        Params = {'Bucket': self.bucket_name, 'Key': self.build_artifact_key_prefix + "/" + repoName + "/" + commitHash + "/" + key}
        if is_gzipped:
            Params["ResponseContentEncoding"] = "gzip"
        Params["ResponseContentType"] = content_type
        if content_type != "text/plain":
            Params["ResponseContentDisposition"] = "attachment; filename=\"" + keyname + "\";"
        else:
            Params["ResponseContentDisposition"] = "inline"

        return FileContents.Redirect(
            self._session.client('s3').generate_presigned_url(
                'get_object', 
                Params = Params, 
                ExpiresIn = 300
                )
            )

    def upload_build(self, repoName, commitHash, key_name, path):
        self._bucket.upload_file(path, self.build_artifact_key_prefix + "/" + repoName + "/" + commitHash + "/" + key_name)

    def download_build(self, repoName, commitHash, key_name, dest):
        self._bucket.download_file(self.build_artifact_key_prefix + "/" + repoName + "/" + commitHash + "/" + key_name, dest)

    def clear_build(self, repoName, commitHash, key_name):
        """Clear a build"""
        self._bucket.Object(self.build_artifact_key_prefix + "/" + repoName + "/" + commitHash + "/" + key_name).delete()

    def build_exists(self, repoName, commitHash, key_name):
        try:
            self._bucket.Object(self.build_artifact_key_prefix + "/" + repoName + "/" + commitHash + "/" + key_name).load()
            return True
        except botocore.exceptions.ClientError as e:
            return False

    def build_size(self, repoName, commitHash, key_name):
        try:
            key = self._bucket.Object(self.build_artifact_key_prefix + "/" + repoName + "/" + commitHash + "/" + key_name).load()
            return None if not key else key.size
        except botocore.exceptions.ClientError as e:
            return False

class LocalArtifactStorage(ArtifactStorage):
    def __init__(self, config):
        ArtifactStorage.__init__(self)
        
        self.build_storage_path = config.path_to_build_artifacts
        self.test_artifacts_storage_path = config.path_to_test_artifacts

    def _buildContents(self, repoName, commitHash, key):  
        with open(os.path.join(self.build_storage_path, repoName, commitHash, key), "r") as f:
            return f.read()

    def buildContentsHtml(self, repoName, commitHash, key):
        content_type, keyname, is_gzipped = ArtifactStorage.keyname_to_encoding(key)

        return FileContents.Inline(
            content_type=content_type,
            content_disposition="attachment; filename=\"" + keyname + "\";",
            content=self._buildContents(repoName, commitHash, key),
            content_encoding="gzip" if is_gzipped else ""
            )
    
    def testContents(self, repoName, commitHash, testId, key):  
        with open(os.path.join(self.test_artifacts_storage_path, repoName, commitHash, testId, key), "r") as f:
            return f.read()

    def testContentsHtml(self, repoName, commitHash, testId, key):
        contents = self.testContents(repoName, commitHash, testId, key)

        content_type, keyname, is_gzipped = ArtifactStorage.keyname_to_encoding(key)

        return FileContents.Inline(
            content_type=content_type,
            content_disposition="attachment; filename=\"" + keyname + "\";" if content_type == "application/octet-stream" else "",
            content=contents,
            content_encoding="gzip" if is_gzipped else ""
            )

    def get_failure_log(self, repoName, commitHash, testId):
        return self.testContents(repoName, commitHash, testId, "test_looper_log.txt")

    def filecopy(self, dest_path, src_path):
        assert not os.path.exists(dest_path), dest_path

        dirname = os.path.split(dest_path)[0]
        try:
            os.makedirs(dirname)
        except OSError:
            pass
        
        try:
            with open(dest_path, "w") as target:
                with open(src_path, "r") as src:
                    shutil.copyfileobj(src, target)
        except:
            if os.path.exists(dest_path):
                os.unlink(dest_path)
            raise

    def testResultKeysFor(self, repoName, commitHash, testId):
        return [x[0] for x in self.testResultKeysForWithSizes(repoName, commitHash, testId)]

    def testResultKeysForWithSizes(self, repoName, commitHash, testId, subprefix=None):
        path = os.path.join(self.test_artifacts_storage_path, repoName, commitHash, testId)

        if subprefix:
            path = os.path.join(path, subprefix)
        
        if not os.path.exists(path):
            return []

        return [(x,os.stat(os.path.join(path,x)).st_size) for x in os.listdir(path)]

    def upload_build(self, repoName, commitHash, key_name, file_name):
        tgt = os.path.join(self.build_storage_path, repoName, commitHash, key_name)
        self.filecopy(tgt, file_name)

    def download_build(self, repoName, commitHash, key_name, dest):
        self.filecopy(dest, os.path.join(self.build_storage_path, repoName, commitHash, key_name))

    def uploadSingleTestArtifact(self, repoName, commitHash, testId, artifact_name, path):
        self.filecopy(os.path.join(self.test_artifacts_storage_path, repoName, commitHash, testId, artifact_name), path)

    def clear_build(self, repoName, commitHash, key_name):
        """Clear a build"""
        path = os.path.join(self.build_storage_path, repoName, commitHash, key_name)
        if os.path.exists(path):
            os.remove(path)

    def build_exists(self, repoName, commitHash, key_name):
        return os.path.exists(os.path.join(self.build_storage_path, repoName, commitHash, key_name))

    def build_size(self, repoName, commitHash, key_name):
        path = os.path.join(self.build_storage_path, repoName, commitHash, key_name)
        if os.path.exists(path):
            return os.stat(path).st_size
        return None
        
def storageFromConfig(config):
    if config.matches.S3:
        return AwsArtifactStorage(config)
    elif config.matches.LocalDisk:
        return LocalArtifactStorage(config)
    else:
        raise Exception("Invalid artifact storage type.")
