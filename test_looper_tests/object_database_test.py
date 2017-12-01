import test_looper.core.algebraic as algebraic
import test_looper.core.object_database as object_database
import test_looper.core.InMemoryJsonStore as InMemoryJsonStore
import unittest
import time

expr = algebraic.Alternative("Expr")
expr.Constant = {'value': int}
expr.Add = {'l': expr, 'r': expr}
expr.Sub = {'l': expr, 'r': expr}
expr.Mul = {'l': expr, 'r': expr}

def initialize_types(db):
    db.Root.define(
        obj=db.Object
        )

    db.Object.define(
        k=expr,
        other=db.Object
        )


class ObjectDatabaseTests(unittest.TestCase):
    def test_basic(self):
        mem_store = InMemoryJsonStore.InMemoryJsonStore()

        db = object_database.Database(mem_store)
        initialize_types(db)

        with db.transaction():
            root = db.Root.New()

            self.assertTrue(root.obj is db.Object.Null)

            root.obj = db.Object.New(k=expr.Constant(value=23))

        db2 = object_database.Database(mem_store)
        initialize_types(db2)

        with db2.view():
            root = db2.Root(root._identity)
            self.assertEqual(root.obj.k.value, 23)

    def test_throughput(self):
        mem_store = InMemoryJsonStore.InMemoryJsonStore()

        db = object_database.Database(mem_store)
        initialize_types(db)

        with db.transaction():
            root = db.Root.New()
            root.obj = db.Object.New(k=expr.Constant(value=0))

        t0 = time.time()
        while time.time() < t0 + 1.0:
            with db.transaction() as t:
                root.obj.k = expr.Constant(value=root.obj.k.value + 1)
        
        with db.view():
            self.assertTrue(root.obj.k.value > 1000, root.obj.k.value)

    def test_transactions(self):
        mem_store = InMemoryJsonStore.InMemoryJsonStore()

        db = object_database.Database(mem_store)
        initialize_types(db)

        with db.transaction():
            root = db.Root.New()

        views = [db.view()]

        for i in [1,2,3]:
            with db.transaction():
                root.obj = db.Object.New(k=expr.Constant(value=i))
            views.append(db.view())

        vals = []
        for v in views:
            with v:
                if root.obj is db.Object.Null:
                    vals.append(None)
                else:
                    vals.append(root.obj.k.value)

        self.assertEqual(vals, [None, 1,2,3])

    def test_conflicts(self):
        mem_store = InMemoryJsonStore.InMemoryJsonStore()

        db = object_database.Database(mem_store)
        initialize_types(db)


        with db.transaction():
            root = db.Root.New()
            root.obj = db.Object.New(k=expr.Constant(value=0))

        for ordering in [0,1]:
            t1 = db.transaction()
            t2 = db.transaction()

            if ordering:
                t1,t2 = t2,t1

            with t1:
                root.obj.k = expr.Constant(value=root.obj.k.value + 1)

            with self.assertRaises(object_database.RevisionConflictException):
                with t2:
                    root.obj.k = expr.Constant(value=root.obj.k.value + 1)
        