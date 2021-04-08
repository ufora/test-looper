import test_looper.core.Bitstring as Bitstring
import unittest
import random


class BitstringTests(unittest.TestCase):
    def test_basic(self):
        for i in range(10):
            random.seed(i + 1)

            length = i * 20

            some_bools = [random.random() > 0.5 for _ in range(length)]
            bitstring = Bitstring.Bitstring.fromBools(some_bools)

            for ix in range(length):
                self.assertEqual(some_bools[ix], bitstring[ix])
