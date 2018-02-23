import os

def nextWord(s, prefix):
    """find the next natural breakpoint of 's' after prefix."""
    if '::' in s[len(prefix):]:
        ix = s[len(prefix):].find('::') + len(prefix)
        return s[:ix + 2]

    return s[:len(prefix)+1] if len(s) > len(prefix) else None

class PrefixTree:
    def __init__(self, strings):
        assert strings
        self.strings = strings
        self.commonPrefix = os.path.commonprefix(strings)

        if len(strings) == 1:
            self.expansions = None

        self.expansions = {}

        for s in strings:
            nextPrefix = nextWord(s, self.commonPrefix)
            if nextPrefix not in self.expansions:
                self.expansions[nextPrefix] = set()
            self.expansions[nextPrefix].add(s)

        self.subtrees = None

    def totalTrees(self):
        if self.subtrees:
            return sum([x.totalTrees() for x in self.subtrees])
        return 1

    def totalLeafSum(self):
        if self.subtrees:
            return sum([x.totalLeafSum() for x in self.subtrees])
        return len(self.strings)

    def totalLeafSumOfSquares(self):
        if self.subtrees:
            return sum([x.totalLeafSumOfSquares() for x in self.subtrees])
        return len(self.strings) * len(self.strings)

    def approximateWeight(self):
        """If this were a bunch of trees of the same size, how many would there be?"""
        return self.totalLeafSum() * self.totalLeafSum() * 1 / self.totalLeafSumOfSquares()

    def explosionImprovement(self):
        #how much does sum of squares decrease if we expand this tree?
        subtreeSizeSum = 0

        for prefix, strings in self.expansions.iteritems():
            subtreeSizeSum += len(strings) * len(strings)

        return (len(self.strings) * len(self.strings) - subtreeSizeSum)

    def bestExplosion(self, maxNewTrees):
        #given that we'll create at most 'maxNewTrees', what is the path of the best 
        #expansion and how much does it reduce sum of squares
        #returns 
        #   (path, newTrees, reduction)

        if self.subtrees:
            best = None
            for ix in xrange(len(self.subtrees)):
                candidate =self.subtrees[ix].bestExplosion(maxNewTrees)

                if candidate and (not best or candidate[2] > best[2]):
                    best = ((ix,) + candidate[0], candidate[1], candidate[2])

            return best
        else:
            if len(self.expansions) - 1 <= maxNewTrees and len(self.expansions) > 1:
                return (), len(self.expansions) - 1, self.explosionImprovement()

    def applyExplosion(self, path):
        if path == ():
            assert not self.subtrees
            self.subtrees = [PrefixTree(strings) for strings in self.expansions.values()]
        else:
            self.subtrees[path[0]].applyExplosion(path[1:])

    def balance(self, maxTrees):
        totalStrings = self.totalLeafSum()

        while True:
            curTrees = self.totalTrees()

            possible = maxTrees - curTrees

            best = self.bestExplosion(possible)

            if not best:
                return

            curSquare = self.totalLeafSumOfSquares()
            newSquare = curSquare - best[2]

            oldRatio = (totalStrings * totalStrings * 1.0) / (curSquare * 1.0)
            newRatio = (totalStrings * totalStrings * 1.0) / (newSquare * 1.0)

            newTrees = best[1]

            if (newRatio - oldRatio) / newTrees < .5:
                return

            self.applyExplosion(best[0])

    def leafPrefixes(self):
        if self.subtrees:
            return [x for child in self.subtrees for x in child.leafPrefixes()]
        return [self.commonPrefix]

    def stringsAndPrefixes(self, out=None):
        """Returns a dict from prefix to [testNames]"""
        if out is None:
            out = {}

        if not self.subtrees:
            for s in self.strings:
                if self.commonPrefix not in out:
                    out[self.commonPrefix] = []
                out[self.commonPrefix].append(s)
        else:
            for s in self.subtrees:
                s.stringsAndPrefixes(out)

        return out






                

