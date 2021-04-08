import test_looper.core.tools.Git as Git
import test_looper_tests.common as common
import logging
import unittest
import tempfile
import shutil
import tarfile
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
        h2 = base_repo.createCommit(
            h1, {"file1": "hi", "dir/file2": "contents"}, "message2"
        )
        base_repo.ensureDetached()

        dep_repo = Git.Git(os.path.join(self.testdir, "dep_repo"))
        dep_repo.cloneFrom(os.path.join(self.testdir, "base_repo"))

        dep_repo2 = Git.Git(os.path.join(self.testdir, "dep_repo_2"))
        dep_repo2.cloneFrom(os.path.join(self.testdir, "base_repo"))

        h2_1 = dep_repo.createCommit(
            h2, {"file1": "hi2_1", "dir/file2": None}, "message3"
        )
        h2_2 = dep_repo.createCommit(
            h2, {"file1": "hi2_2", "dir/file2": None}, "message3\n\nnewline\n\n\n"
        )

        dep_repo.pushCommit(h2_1, "master")
        self.assertTrue(base_repo.commitExists(h2_1))

        self.assertFalse(dep_repo.pushCommit(h2_2, "master"))
        self.assertFalse(base_repo.commitExists(h2_2))

        self.assertTrue(dep_repo.pushCommit(h2_2, "master", force=True))
        self.assertTrue(base_repo.commitExists(h2_2))

        self.assertFalse(dep_repo.pushCommit(h2_2, "new_branch"))
        self.assertTrue(dep_repo.pushCommit(h2_2, "new_branch", createBranch=True))
        self.assertTrue(dep_repo.pushCommit(h2_2, "new_branch", createBranch=True))

        self.assertTrue("new_branch" in dep_repo.listBranchesForRemote("origin"))

        self.assertTrue(
            "new_branch" not in dep_repo2.listCurrentlyKnownBranchesForRemote("origin")
        )
        self.assertTrue("new_branch" in dep_repo2.listBranchesForRemote("origin"))

        dep_repo2.fetchOrigin()

        self.assertTrue(
            "new_branch" in dep_repo2.listCurrentlyKnownBranchesForRemote("origin")
        )

        h2_2_info, h2_info = dep_repo.gitCommitDataMulti(h2_2, 2)

        self.assertEqual(h2_2_info[0], h2_2)
        self.assertEqual(h2_2_info[1], [h2])
        self.assertEqual(h2_2_info[3], "message3\n\nnewline")

        self.assertEqual(h2_info[0], h2)
        self.assertEqual(h2_info[1], [h1])
        self.assertEqual(h2_info[3], "message2")

    def test_most_recent_hash_for(self):
        base_repo = Git.Git(os.path.join(self.testdir, "base_repo"))
        base_repo.init()

        h1 = base_repo.commit("message1")
        h2 = base_repo.createCommit(
            h1,
            {"file1": "hi", "dir1/file2": "contents", "dir2/file3": "contents"},
            "message2",
        )
        h3 = base_repo.createCommit(h2, {"dir1/file2": "contents_2"}, "message3")
        h4 = base_repo.createCommit(h3, {"dir2/file2": "contents_2"}, "message4")
        h5 = base_repo.createCommit(
            h4, {"dir2/file2": "contents_2", "dir2": None}, "message4"
        )

        self.assertEqual(base_repo.mostRecentHashForSubpath(h1, "dir1"), None)
        self.assertEqual(base_repo.mostRecentHashForSubpath(h1, "dir2"), None)

        self.assertEqual(base_repo.mostRecentHashForSubpath(h2, "dir1"), h2)
        self.assertEqual(base_repo.mostRecentHashForSubpath(h2, "dir2"), h2)

        self.assertEqual(base_repo.mostRecentHashForSubpath(h4, "dir1"), h3)
        self.assertEqual(base_repo.mostRecentHashForSubpath(h4, "dir2"), h4)

        self.assertEqual(base_repo.mostRecentHashForSubpath(h5, "dir2"), h5)

        # what happens if 'dir1/file2' changes but becomes the same thing in two different pathways?
        h6_left = base_repo.createCommit(h5, {"dir1/file2": "contents_left"}, "message")
        h6_left_2 = base_repo.createCommit(
            h6_left, {"dir1/file2": "contents_final"}, "message"
        )

        h6_right = base_repo.createCommit(
            h5, {"dir1/file2": "contents_right"}, "message"
        )
        h6_right_2 = base_repo.createCommit(
            h6_right, {"dir1/file2": "contents_final"}, "message"
        )

        h7 = base_repo.createMerge(h6_left_2, [h6_right_2], "merge commit")
        self.assertEqual(base_repo.mostRecentHashForSubpath(h7, "dir1"), h6_left_2)

    def test_create_sub_tarball(self):
        base_repo = Git.Git(os.path.join(self.testdir, "base_repo"))
        base_repo.init()

        h1 = base_repo.commit("message1")
        h2 = base_repo.createCommit(
            h1,
            {"file1": "hi", "dir1/file2": "contents", "dir2/file3": "contents"},
            "message2",
        )
        h3 = base_repo.createCommit(
            h1,
            {
                "file1": "hi",
                "dir1/file2": "contents",
                "dir2/file3": "contents\ncontents_second_line",
            },
            "message2",
        )

        tarball_dir2 = os.path.join(self.testdir, "output_dir2.tar.gz")
        tarball = os.path.join(self.testdir, "output.tar.gz")
        tarball_crlf = os.path.join(self.testdir, "output_crlf.tar.gz")
        tarball_h3_crlf = os.path.join(self.testdir, "output_h3_crlf.tar.gz")
        tarball_h3 = os.path.join(self.testdir, "output_h3.tar.gz")

        base_repo.createRepoTarball(h2, "", tarball, False)
        base_repo.createRepoTarball(h2, "dir2", tarball_dir2, False)
        base_repo.createRepoTarball(h2, "", tarball_crlf, True)
        base_repo.createRepoTarball(h3, "", tarball_h3_crlf, True)
        base_repo.createRepoTarball(h3, "", tarball_h3, False)

        self.assertTrue(os.path.exists(tarball))
        self.assertTrue(os.path.exists(tarball_dir2))
        self.assertTrue(os.path.exists(tarball_crlf))
        self.assertTrue(os.path.exists(tarball_h3_crlf))
        self.assertTrue(os.path.exists(tarball_h3))

        self.assertTrue(os.stat(tarball_dir2).st_size < os.stat(tarball).st_size)

        with tarfile.open(tarball) as tf:
            self.assertEqual(
                sorted(tf.getnames()),
                sorted(
                    [
                        ".",
                        "./.git_commit",
                        "./file1",
                        "./dir1",
                        "./dir1/file2",
                        "./dir2",
                        "./dir2/file3",
                    ]
                ),
            )

        with tarfile.open(tarball_dir2) as tf:
            self.assertEqual(
                sorted(tf.getnames()), sorted([".", "./.git_commit", "./file3"])
            )

        with tarfile.open(tarball_crlf) as tf:
            self.assertEqual(tf.extractfile("./dir2/file3").read(), b"contents")
        with tarfile.open(tarball) as tf:
            self.assertEqual(tf.extractfile("./dir2/file3").read(), b"contents")

        with tarfile.open(tarball_h3) as tf:
            self.assertEqual(
                tf.extractfile("./dir2/file3").read(), b"contents\ncontents_second_line"
            )
        with tarfile.open(tarball_h3_crlf) as tf:
            self.assertEqual(
                tf.extractfile("./dir2/file3").read(),
                b"contents\r\ncontents_second_line",
            )
