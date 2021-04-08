import unittest
import tempfile
import uuid
import boto3
import os
import shutil
import test_looper.core.Config as Config
import test_looper.core.ArtifactStorage as ArtifactStorage
import io
import tarfile
import requests

def put_into(dir, things):
    for itemname, item in things.items():
        if isinstance(item, dict):
            try:
                os.makedirs(os.path.join(dir, itemname))
            except:
                pass
            put_into(os.path.join(dir, itemname), item)
        else:
            with open(os.path.join(dir, itemname), "wb") as f:
                f.write(item)

class Mixin:
    def contentsOfTestArtifact(self, testHash, testId, artifactName):
        contents = self.artifactStorage.testContentsHtml(testHash, testId, artifactName)

        if contents.matches.Redirect:
            r = requests.get(contents.url)
            self.assertEqual(r.status_code, 200)

            return ArtifactStorage.FileContents.Inline(
                content_type=r.headers.get("content-type",""), 
                content_encoding=r.headers.get("content-encoding",""), 
                content_disposition=r.headers.get("content-disposition",""), 
                content=r.content
                )
        else:
            return contents

    def test_upload_build(self):
        put_into(self.scratchdir, {"worker": {"out.tar.gz": b"some_tarball"}})

        self.assertFalse(self.artifactStorage.build_exists("testhash", "build_key"))
        self.artifactStorage.upload_build("testhash", "build_key", os.path.join(self.scratchdir, "worker", "out.tar.gz"))
        self.assertTrue(self.artifactStorage.build_exists("testhash", "build_key"))
        self.artifactStorage.download_build("testhash", "build_key", os.path.join(self.scratchdir, "worker", "out2.tar.gz"))

        self.assertEqual(open(os.path.join(self.scratchdir, "worker", "out2.tar.gz"), "rb").read(), b"some_tarball")


class LocalArtifactStorageTest(unittest.TestCase, Mixin):
    def setUp(self):
        self.testdir = tempfile.mkdtemp()
        self.scratchdir = tempfile.mkdtemp()

        self.artifactStorage = ArtifactStorage.storageFromConfig(
            Config.ArtifactsConfig.LocalDisk(
                path_to_build_artifacts=os.path.join(self.testdir, "builds"),
                path_to_test_artifacts=os.path.join(self.testdir, "tests")
                )
            )

    def tearDown(self):
        shutil.rmtree(self.testdir)
        shutil.rmtree(self.scratchdir)

    def testNameSymmetry(self):
        def test(x):
            self.assertEqual(ArtifactStorage.ArtifactStorage.unsanitizeName(ArtifactStorage.ArtifactStorage.sanitizeName(x)), x)
        test("asdf")
        test("as\\df_")
        test("a:  s/df_")
        test("a:  s/df_")

test_with_real_aws = False
if test_with_real_aws:
    class AwsArtifactStorageTest(unittest.TestCase, Mixin):
        def setUp(self):
            self.s3 = boto3.Session(region_name="us-east-1").resource("s3")
            self.scratchdir = tempfile.mkdtemp()
            self.bucketname = "testlooper-test-" + str(uuid.uuid4())
            self.bucket = self.s3.create_bucket(Bucket=self.bucketname, ACL="private")

            self.artifactStorage = ArtifactStorage.storageFromConfig(
                Config.ArtifactsConfig.S3(
                    bucket=self.bucketname,
                    region="us-east-1",
                    build_artifact_key_prefix="builds",
                    test_artifact_key_prefix="tests"
                    )
                )

        def tearDown(self):
            self.bucket.objects.delete()
            self.bucket.delete()
            shutil.rmtree(self.scratchdir)


