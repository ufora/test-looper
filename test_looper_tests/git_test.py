import test_looper.core.tools.Git as Git
import test_looper_tests.common as common
import logging
import unittest
import tempfile
import shutil
import os

class GitTests(unittest.TestCase):
    def setUp(self):
        common.configureLogging(verbose=True)
        logging.info("WorkerStateTests set up")
        self.testdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.testdir)

    def test_basic(self):
        base_repo = Git.Git(os.path.join(self.testdir, "base_repo"))
        base_repo.init()

        h1 = base_repo.commit("message1")
        h2 = base_repo.createCommit(h1, {'file1': "hi", "dir/file2": "contents"}, "message2")
        base_repo.ensureDetached()

        dep_repo = Git.Git(os.path.join(self.testdir, "dep_repo"))
        dep_repo.cloneFrom(os.path.join(self.testdir, "base_repo"))

        dep_repo2 = Git.Git(os.path.join(self.testdir, "dep_repo_2"))
        dep_repo2.cloneFrom(os.path.join(self.testdir, "base_repo"))

        h2_1 = dep_repo.createCommit(h2, {'file1': 'hi2_1', "dir/file2": None}, "message3")
        h2_2 = dep_repo.createCommit(h2, {'file1': 'hi2_2', "dir/file2": None}, "message3\n\nnewline\n\n\n")

        dep_repo.pushCommit(h2_1, "master")
        self.assertTrue(base_repo.commitExists(h2_1))

        self.assertFalse(dep_repo.pushCommit(h2_2, "master"))
        self.assertFalse(base_repo.commitExists(h2_2))

        self.assertTrue(dep_repo.pushCommit(h2_2, "master", force=True))
        self.assertTrue(base_repo.commitExists(h2_2))

        self.assertFalse(dep_repo.pushCommit(h2_2, "new_branch"))
        self.assertTrue(dep_repo.pushCommit(h2_2, "new_branch", createBranch=True))
        self.assertTrue(dep_repo.pushCommit(h2_2, "new_branch", createBranch=True))

        self.assertTrue("new_branch" in dep_repo.listBranchesForRemote('origin'))


        self.assertTrue("new_branch" not in dep_repo2.listCurrentlyKnownBranchesForRemote('origin'))
        self.assertTrue("new_branch" in dep_repo2.listBranchesForRemote('origin'))

        dep_repo2.fetchOrigin()
        
        self.assertTrue("new_branch" in dep_repo2.listCurrentlyKnownBranchesForRemote('origin'))

        h2_2_info, h2_info = dep_repo.gitCommitDataMulti(h2_2, 2)

        self.assertEqual(h2_2_info[0], h2_2)
        self.assertEqual(h2_2_info[1], [h2])
        self.assertEqual(h2_2_info[3], "message3\n\nnewline")

        self.assertEqual(h2_info[0], h2)
        self.assertEqual(h2_info[1], [h1])
        self.assertEqual(h2_info[3], "message2")
        