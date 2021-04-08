import test_looper.core.algebraic as algebraic
import test_looper.core.object_database as object_database
import test_looper.core.InMemoryJsonStore as InMemoryJsonStore
import unittest
import random
import time



class ObjectDatabaseCacheTests(unittest.TestCase):
    def setUp(self):
        self.mem_store = InMemoryJsonStore.InMemoryJsonStore()
        self.db = object_database.Database(self.mem_store)
        self.nodes = []

        self.db.Node.define(x=int, count=float)
        self.db.addIndex(self.db.Node, 'x')

        with self.db.transaction():
            for x in range(100):
                self.nodes.append(self.db.Node.New(x=x,count=0.0))

        self.functionRunCount = 0
        self.functionCalls = []
        def computeAvg(x, depth):
            self.functionRunCount += 1
            self.functionCalls.append((x,depth))

            if depth == 0:
                if x < 0 or x >= len(self.nodes):
                    return 0.0

                return self.nodes[x].count

            lhs = self.db.lookupCachedCalculation("avg", ((x-1), depth-1))
            rhs = self.db.lookupCachedCalculation("avg", ((x+1), depth-1))

            return (lhs+rhs)/2

        self.db.addCalculationCache("avg", computeAvg)

    def addUpAll(self, depth, viewToUse = None):
        res = 0
        with viewToUse or self.db.view():
            for x in range(-1 - depth, 100 + depth + 1):
                res += self.db.lookupCachedCalculation("avg", (x,depth))
        return res

    def test_cache_basic(self):
        self.assertEqual(self.addUpAll(depth=0), 0.0)
        
        functionRunCount = self.functionRunCount

        self.assertEqual(self.addUpAll(depth=0), 0.0)

        self.assertEqual(self.addUpAll(depth=0), 0.0)
        
        #we shouldn't have re-run the cache function if the cache is working
        self.assertEqual(functionRunCount, self.functionRunCount)

        with self.db.transaction():
            self.nodes[50].count = 1.0

        self.assertEqual(self.addUpAll(depth=0), 1.0)
        self.assertEqual(functionRunCount + 1, self.functionRunCount)

        #cache a little deeper
        self.assertEqual(self.addUpAll(depth=2), 1.0)

        self.functionCalls = []

        with self.db.transaction():
            self.nodes[50].count = 2.0

        self.assertEqual(self.addUpAll(depth=0), 2.0)
        self.assertEqual(self.addUpAll(depth=1), 2.0)
        self.assertEqual(self.addUpAll(depth=2), 2.0)

        self.assertEqual(sorted(self.functionCalls), sorted(
            [(50,0), (49,1),(51,1), (48,2),(50,2),(52,2)]
            ))
        

    def test_cache_invalidation(self):
        with self.db.transaction() as t:
            tIDRoot = t._transaction_num

        random.seed(1)

        self.assertEqual(round(self.addUpAll(depth=100),1), 0)

        someOldViews = []

        for update in range(1000):
            origRunCount = self.functionRunCount

            with self.db.transaction():
                node = random.choice(self.nodes)
                node.count = node.count + 1.0

            self.assertEqual(round(self.addUpAll(depth=10),1), update+1)
            self.assertEqual(self.functionRunCount - origRunCount, 12 * 11 / 2)

            if random.random() < .5:
                someOldViews.append( (self.db.view(), update+1) )

            if someOldViews and random.random() < .5 or len(someOldViews) > 5:
                view, targetAddValue = someOldViews.pop()
                #invalid caches take _much_ longer to calculate.
                self.assertEqual(round(self.addUpAll(depth=4,viewToUse=view),1), targetAddValue)


