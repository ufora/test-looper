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

    db.Counter.define(k=int)


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
    
    def test_read_write_conflict(self):
        mem_store = InMemoryJsonStore.InMemoryJsonStore()

        db = object_database.Database(mem_store)
        initialize_types(db)

        with db.transaction():
            o1 = db.Counter.New()
            o2 = db.Counter.New()

        t1 = db.transaction()
        t2 = db.transaction()

        with t1.nocommit():
            o1.k = o2.k + 1
        
        with t2.nocommit():
            o2.k = o1.k + 1

        t1.commit()

        with self.assertRaises(object_database.RevisionConflictException):
            t2.commit()
        
    def test_indices(self):
        mem_store = InMemoryJsonStore.InMemoryJsonStore()

        db = object_database.Database(mem_store)
        initialize_types(db)
        db.addIndex(db.Counter, 'k')

        with db.view() as v:
            self.assertEqual(v.indexLookup(db.Counter,k=20), ())
            self.assertEqual(v.indexLookup(db.Counter,k=30), ())

        with db.transaction():
            o1 = db.Counter.New(k = 20)

        with db.view() as v:
            self.assertEqual(v.indexLookup(db.Counter,k=20), (o1,))
            self.assertEqual(v.indexLookup(db.Counter,k=30), ())

        with db.transaction():
            o1.k = 30

        with db.view() as v:
            self.assertEqual(v.indexLookup(db.Counter,k=20), ())
            self.assertEqual(v.indexLookup(db.Counter,k=30), (o1,))

        with db.transaction():
            o1.delete()

        with db.view() as v:
            self.assertEqual(v.indexLookup(db.Counter,k=20), ())
            self.assertEqual(v.indexLookup(db.Counter,k=30), ())

    def test_indices_of_algebraics(self):
        mem_store = InMemoryJsonStore.InMemoryJsonStore()

        db = object_database.Database(mem_store)
        initialize_types(db)
        db.addIndex(db.Object, 'k')

        with db.transaction():
            o1 = db.Object.New(k=expr.Constant(value=123))

        with db.view() as v:
            self.assertEqual(v.indexLookup(db.Object,k=expr.Constant(value=123)), (o1,))

    def test_index_functions(self):
        mem_store = InMemoryJsonStore.InMemoryJsonStore()

        db = object_database.Database(mem_store)
        db.Object.define(k=int)
        db.addIndex(db.Object, 'k')
        db.addIndex(db.Object, 'k2', lambda o: o.k * 2)
        
        with db.transaction():
            o1 = db.Object.New(k=10)

        with db.view() as v:
            self.assertEqual(v.indexLookup(db.Object,k=10), (o1,))
            self.assertEqual(v.indexLookup(db.Object,k2=20), (o1,))
            self.assertEqual(v.indexLookup(db.Object,k=20), ())

    def test_index_functions_None_semantics(self):
        mem_store = InMemoryJsonStore.InMemoryJsonStore()

        db = object_database.Database(mem_store)
        db.Object.define(k=int)
        db.addIndex(db.Object, 'index', lambda o: True if o.k > 10 else None)
        
        with db.transaction() as v:
            self.assertEqual(v.indexLookup(db.Object,index=True), ())
            o1 = db.Object.New(k=10)
            self.assertEqual(v.indexLookup(db.Object,index=True), ())
            o1.k = 20
            self.assertEqual(v.indexLookup(db.Object,index=True), (o1,))
            o1.k = 10
            self.assertEqual(v.indexLookup(db.Object,index=True), ())
            o1.k = 20
            self.assertEqual(v.indexLookup(db.Object,index=True), (o1,))
            o1.delete()
            self.assertEqual(v.indexLookup(db.Object,index=True), ())

    def test_indices_update_during_transactions(self):
        mem_store = InMemoryJsonStore.InMemoryJsonStore()

        db = object_database.Database(mem_store)
        db.Object.define(k=int)
        db.addIndex(db.Object, 'k')
        
        with db.transaction() as v:
            self.assertEqual(v.indexLookup(db.Object,k=10), ())
            o1 = db.Object.New(k=10)

            self.assertEqual(v.indexLookup(db.Object,k=10), (o1,))
            
            o1.k = 20

            self.assertEqual(v.indexLookup(db.Object,k=10), ())
            self.assertEqual(v.indexLookup(db.Object,k=20), (o1,))

            o1.delete()

            self.assertEqual(v.indexLookup(db.Object,k=10), ())
            self.assertEqual(v.indexLookup(db.Object,k=20), ())

    def test_index_transaction_conflicts(self):
        mem_store = InMemoryJsonStore.InMemoryJsonStore()

        db = object_database.Database(mem_store)
        db.Object.define(k=int)
        db.Other.define(k=int)
        db.addIndex(db.Object, 'k')
        
        with db.transaction():
            o1 = db.Object.New(k=10)
            o2 = db.Object.New(k=20)
            o3 = db.Object.New(k=30)

        t1 = db.transaction()
        t2 = db.transaction()

        with t1.nocommit():
            o2.k=len(t1.indexLookup(db.Object,k=10))

        with t2.nocommit():
            o1.k = 20

        t2.commit()

        with self.assertRaises(object_database.RevisionConflictException):
            t1.commit()

    def test_default_constructor_for_list(self):
        mem_store = InMemoryJsonStore.InMemoryJsonStore()

        db = object_database.Database(mem_store)
        db.Object.define(x = algebraic.List(int))

        with db.transaction():
            n = db.Object.New()
            self.assertEqual(len(n.x), 0)
