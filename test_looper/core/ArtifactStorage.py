import boto3
import botocore
import threading
import os
import time
import logging
import traceback
import tempfile
import tarfile
import shutil
import gzip
import re
import Queue
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

TEST_LOG_NAME_PREFIX = "individual_test_logs/"

class ArtifactStorage(object):
    @staticmethod
    def keyname_to_encoding(key):
        if key.endswith(".out.gz") or key.endswith(".log.gz") or key.endswith(".txt.gz") or key.endswith(".stdout.gz") or key.endswith(".stderr.gz"):
            return ("text/plain", key[:-3], True)
        if key.endswith(".txt") or key.endswith(".log") or key.endswith(".out"):
            return ("text/plain", key, False)
        if key.endswith(".stdout") or key.endswith(".stderr"):
            return ("text/plain", key, False)
        if key.endswith(".png"):
            return ("image/png", key, False)
        return ("application/octet-stream", key, False)

    @staticmethod
    def sanitizeName(name):
        return name.replace("_", "_u_").replace("/","_s_").replace("\\", "_bs_").replace(":","_c_").replace(" ","_sp_")

    @staticmethod
    def unsanitizeName(name):
        #every single '_' should be followed by a character or two and another underscore. each of these
        #has a single distinct mapping that describes what to do with it to invert the name
        pat = re.compile("_(u|s|bs|c|sp)_")
        result = pat.split(name)

        lookup = {"u":"_", "s":"/", "bs": "\\", "c": ":", "sp": " "}
        result[1::2] = [lookup[val] for val in result[1::2]]
        return "".join(result)

    def testResultKeysAndSizesForIndividualTest(self, testHash, testId, testName, testRunIx):
        subPrefix = TEST_LOG_NAME_PREFIX + self.sanitizeName(testName) + "/" + str(testRunIx)

        res = []

        for key,sz in self.testResultKeysForWithSizes(testHash, testId, subPrefix):
            res.append((subPrefix + "/" + key, sz))
        
        return res

    def uploadIndividualTestArtifacts(self, testHash, testId, pathsToUpload, logger=None):
        queue = Queue.Queue()

        def uploadArtifact(testName, runIx, path):
            try:
                testName = self.sanitizeName(testName)
                filename = os.path.basename(path)

                self.uploadSingleTestArtifact(
                    testHash, 
                    testId, 
                    TEST_LOG_NAME_PREFIX + testName + "/" + str(runIx) + "/" + filename, 
                    path
                    )
            except:
                logging.error("Failed to upload %s:\n%s", path, traceback.format_exc())
            finally:
                queue.put(filename)

        counts = 0

        for (testName, runIx), paths in pathsToUpload.iteritems():
            for path in paths:
                timerQueue.enqueueWorkItem(uploadArtifact, (testName, runIx, path))
                counts += 1

        try:
            if logger:
                logger("Uploading a total of %s artifacts" % counts)

            while counts:
                item = queue.get(timeout=360)
                counts -= 1
                if counts % 10 == 0 and logger:
                    logger("%s artifacts remaining" % counts)
        except Queue.Empty:
            if logger:
                logger("Timed out uploading individual artifacts. %s remaining" % counts)
            raise Exception("Timed out uploading individual artifacts")


    def testResultKeysFor(self, testHash, testId):
        """Return a list of test results for a given testId.

        testId: str
        returns: list of strings with result keys
        """
        assert False, "Subclasses implement"

    def testContentsHtml(self, testHash, testId, key):
        """Get a FileContents for a given testId and key"""
        assert False, "Subclasses implement"

    def uploadSingleTestArtifact(self, testHash, testId, artifact_name, path):
        """Upload a single file as a test artifact for 'testId'"""
        assert False, "Subclasses implement"

    def buildContentsHtml(self, testHash, key):
        """Get a FileContents for a given build key"""
        assert False, "Subclasses implement"

    def upload_build(self, testHash, key_name, file_name):
        """Upload a build in 'file_name' to build key 'key_name'"""
        assert False, "Subclasses implement"

    def download_build(self, testHash, key_name, dest):
        """Download a build in 'key_name' to 'dest'"""
        assert False, "Subclasses implement"

    def clear_build(self, testHash, key_name):
        """Clear a build"""
        assert False, "Subclasses implement"

    def build_exists(self, testHash, key_name):
        """Returns true if a build with 'key_name' exists. False otherwise."""
        assert False, "Subclasses implement"

    def uploadSourceTarball(self, git_repo, commitHash, subpath, platform):
        assert platform in ['linux', 'win']

        source_platform_name = "source-" + platform
    
        if subpath:            
            source_platform_name = source_platform_name + "/" + subpath

        artifact_key = self.sanitizeName(source_platform_name) + ".tar.gz"
            
        if not self.build_exists(commitHash, artifact_key):
            tarballs_dir = tempfile.mkdtemp(dir=self.tempfileOverrideDir or None)

            try:
                tarball_name = os.path.join(tarballs_dir, artifact_key)

                git_repo.createRepoTarball(commitHash, subpath, tarball_name, setCoreAutocrlf=platform=="win")

                logging.info("ArtifactStorage uploading %s/%s", commitHash, artifact_key)
                self.upload_build(commitHash, artifact_key, tarball_name)
            finally:
                try:
                    shutil.rmtree(tarballs_dir)
                except:
                    logging.error("ArtifactStorage: Failed to remove dir %s:\n%s", tarballs_dir, traceback.format_exc())
        else:
            logging.info("ArtifactStorage already had a build for %s/%s", commitHash, artifact_key)

class AwsArtifactStorage(ArtifactStorage):
    def __init__(self, config):
        ArtifactStorage.__init__(self)

        self.bucket_name = config.bucket
        self.region = config.region
        self.build_artifact_key_prefix = config.build_artifact_key_prefix
        self.test_artifact_key_prefix = config.test_artifact_key_prefix
        self.tempfileOverrideDir = None

    @property
    def _session(self):
        return boto3.Session(region_name=self.region)

    @property
    def _bucket(self):
        return self._session.resource('s3').Bucket(self.bucket_name)

    def testResultKeysForWithSizes(self, testHash, testId, subPrefix=None):
        prefix = self.test_artifact_key_prefix + "/" + testHash + "/" + testId + "/"

        if subPrefix:
            prefix = prefix + subPrefix + "/"

        keys = list(self._bucket.objects.filter(Prefix=prefix))

        result = []

        for k in keys:
            assert k.key.startswith(prefix)
            result.append((k.key[len(prefix):],k.size))

        return result

    
    def testResultKeysFor(self, testHash, testId):
        return [x[0] for x in self.testResultKeysForWithSizes(testHash, testId)]

    def uploadSingleTestArtifact(self, testHash, testId, key, full_path):
        content_type, keyname, is_gzipped = ArtifactStorage.keyname_to_encoding(key)

        with open(full_path, "rb") as f:
            kwargs = {}
            if is_gzipped:
                kwargs["ContentEncoding"] = "gzip"

            if content_type not in ("text/plain", "image/png"):
                kwargs["ContentDisposition"]="attachment; filename=\"" + keyname + "\";"

            self._bucket.put_object(
                Body=f,
                ContentType=content_type,
                Key=self.test_artifact_key_prefix + "/" + testHash + "/" + testId + "/" + key,
                **kwargs
                )

    def testContentsHtml(self, testHash, testId, key):
        content_type, keyname, is_gzipped = ArtifactStorage.keyname_to_encoding(key)
            
        Params = {'Bucket': self.bucket_name, 'Key': self.test_artifact_key_prefix + "/" + testHash + "/" + testId + "/" + key}
        if is_gzipped:
            Params["ResponseContentEncoding"] = "gzip"
        Params["ResponseContentType"] = content_type
        if content_type not in ("text/plain", "image/png"):
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

    def buildContentsHtml(self, testHash, key):
        content_type, keyname, is_gzipped = ArtifactStorage.keyname_to_encoding(key)
            
        Params = {'Bucket': self.bucket_name, 'Key': self.build_artifact_key_prefix + "/" + testHash + "/" + key}
        if is_gzipped:
            Params["ResponseContentEncoding"] = "gzip"
        Params["ResponseContentType"] = content_type
        if content_type not in ("text/plain", "image/png"):
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

    def upload_build(self, testHash, key_name, path):
        self._bucket.upload_file(path, self.build_artifact_key_prefix + "/" + testHash + "/" + key_name)

    def download_build(self, testHash, key_name, dest):
        self._bucket.download_file(self.build_artifact_key_prefix + "/" + testHash + "/" + key_name, dest)

    def clear_build(self, testHash, key_name):
        """Clear a build"""
        self._bucket.Object(self.build_artifact_key_prefix + "/" + testHash + "/" + key_name).delete()

    def build_exists(self, testHash, key_name):
        try:
            self._bucket.Object(self.build_artifact_key_prefix + "/" + testHash + "/" + key_name).load()
            return True
        except botocore.exceptions.ClientError as e:
            return False

    def build_size(self, testHash, key_name):
        try:
            key = self._bucket.Object(self.build_artifact_key_prefix + "/" + testHash + "/" + key_name)
            key.load()
            return None if not key else key.content_length
        except botocore.exceptions.ClientError as e:
            return False

class LocalArtifactStorage(ArtifactStorage):
    def __init__(self, config):
        ArtifactStorage.__init__(self)
        
        self.build_storage_path = config.path_to_build_artifacts
        self.test_artifacts_storage_path = config.path_to_test_artifacts
        self.tempfileOverrideDir = None

    def _buildContents(self, testHash, key):  
        with open(os.path.join(self.build_storage_path, testHash, key), "r") as f:
            return f.read()

    def buildContentsHtml(self, testHash, key):
        content_type, keyname, is_gzipped = ArtifactStorage.keyname_to_encoding(key)

        return FileContents.Inline(
            content_type=content_type,
            content_disposition="attachment; filename=\"" + keyname + "\";",
            content=self._buildContents(testHash, key),
            content_encoding="gzip" if is_gzipped else ""
            )
    
    def testContents(self, testHash, testId, key):  
        with open(os.path.join(self.test_artifacts_storage_path, testHash, testId, key), "r") as f:
            return f.read()

    def testContentsHtml(self, testHash, testId, key):
        contents = self.testContents(testHash, testId, key)

        content_type, keyname, is_gzipped = ArtifactStorage.keyname_to_encoding(key)

        return FileContents.Inline(
            content_type=content_type,
            content_disposition="attachment; filename=\"" + keyname + "\";" if content_type == "application/octet-stream" else "",
            content=contents,
            content_encoding="gzip" if is_gzipped else ""
            )

    def get_failure_log(self, testHash, testId):
        return self.testContents(testHash, testId, "test_looper_log.txt")

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

    def testResultKeysFor(self, testHash, testId):
        return [x[0] for x in self.testResultKeysForWithSizes(testHash, testId)]

    def testResultKeysForWithSizes(self, testHash, testId, subprefix=None):
        path = os.path.join(self.test_artifacts_storage_path, testHash, testId)

        if subprefix:
            path = os.path.join(path, subprefix)
        
        if not os.path.exists(path):
            return []

        return [(x,os.stat(os.path.join(path,x)).st_size) for x in os.listdir(path)]

    def upload_build(self, testHash, key_name, file_name):
        tgt = os.path.join(self.build_storage_path, testHash, key_name)
        self.filecopy(tgt, file_name)

    def download_build(self, testHash, key_name, dest):
        self.filecopy(dest, os.path.join(self.build_storage_path, testHash, key_name))

    def uploadSingleTestArtifact(self, testHash, testId, artifact_name, path):
        self.filecopy(os.path.join(self.test_artifacts_storage_path, testHash, testId, artifact_name), path)

    def clear_build(self, testHash, key_name):
        """Clear a build"""
        path = os.path.join(self.build_storage_path, testHash, key_name)
        if os.path.exists(path):
            os.remove(path)

    def build_exists(self, testHash, key_name):
        return os.path.exists(os.path.join(self.build_storage_path, testHash, key_name))

    def build_size(self, testHash, key_name):
        path = os.path.join(self.build_storage_path, testHash, key_name)
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
