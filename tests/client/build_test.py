import os
import random
import string
import subprocess
import tempfile
import unittest

import test_looper.client.build as build


def random_string(length, source_alpha=string.printable):
    return ''.join(random.choice(source_alpha) for _ in xrange(length))


class BuildTests(unittest.TestCase):
    def test_hash_dir_one_file(self):
        tmp_dir = tempfile.mkdtemp()
        fd, file_path = tempfile.mkstemp(dir=tmp_dir)
        with os.fdopen(fd, 'w') as f:
            f.write(random_string(5000))

        actual_hash = build.hash_files_in_path(tmp_dir)
        expected_hash = subprocess.check_output(
            "md5sum %s | awk '{print $1}'" % file_path,
            shell=True
            ).rstrip()
        self.assertEqual(expected_hash, actual_hash)


    def test_hash_dir_multiple_files(self):
        tmp_dir = tempfile.mkdtemp()

        for _ in xrange(10):
            with os.fdopen(tempfile.mkstemp(dir=tmp_dir)[0], 'w') as f:
                f.write(random_string(5000))

        actual_hash = build.hash_files_in_path(tmp_dir)
        expected_hash = subprocess.check_output(
            "cat %s | md5sum | awk '{print $1}'" % os.path.join(tmp_dir, '*'),
            shell=True
            ).rstrip()
        self.assertEqual(expected_hash, actual_hash)






if __name__ == '__main__':
    unittest.main()
