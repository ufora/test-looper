import test_looper.core.PrefixTree as PrefixTree
import unittest

some_strings = [
    "aaabc",
    "aaabd",
    "aaabe1",
    "aaabe2"
    ]

class PrefixTreeTest(unittest.TestCase):
    def test_basic(self):
        tree = PrefixTree.PrefixTree(some_strings)
        tree.balance(4)
        self.assertEqual(sorted(tree.leafPrefixes()), ["aaabc", "aaabd", "aaabe1", "aaabe2"])

    def test_restrict_treecount(self):
        tree = PrefixTree.PrefixTree(some_strings)
        tree.balance(3)
        self.assertEqual(sorted(tree.leafPrefixes()), ["aaabc", "aaabd", "aaabe"])

    def test_dont_over_expand(self):
        tree = PrefixTree.PrefixTree([
                "aaaba",
                "aaabd"
                ] + ["aaabe%s" % ix for ix in xrange(3,8)])

        tree.balance(4)

        self.assertEqual(sorted(tree.leafPrefixes()), ["aaab"])

        