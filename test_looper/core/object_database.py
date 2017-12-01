import test_looper.core.algebraic as algebraic
import test_looper.core.algebraic_to_json as algebraic_to_json
from test_looper.core.hash import sha_hash
import threading
import uuid

_encoder = algebraic_to_json.Encoder()

_cur_view = threading.local()

class RevisionConflictException(Exception):
    pass

#singleton object that clients should never see
_creating_the_null_object = []

class DatabaseObject(object):
    __algebraic__ = True
    __types__ = None
    _database = None
    Null = None

    def __init__(self, identity):
        object.__init__(self)

        if identity is _creating_the_null_object:
            if type(self).Null is None:
                type(self).Null = self
            identity = "NULL"
        else:
            assert isinstance(identity, str), type(identity)

        self.__dict__['_identity'] = identity

    @classmethod
    def __default_initializer__(cls):
        return cls.Null

    @classmethod
    def New(cls, **kwds):
        if not hasattr(_cur_view, "view"):
            raise Exception("Please create new objects from within a transaction.")

        if _cur_view.view._db is not cls._database:
            raise Exception("Please create new objects from within a transaction created on the same database as the object.")

        return _cur_view.view._new(cls, kwds)

    def __getattr__(self, name):
        if self.__dict__["_identity"] == "NULL":
            raise Exception("Null object has no fields")

        if name[:1] == "_":
            raise AttributeError(name)

        if name not in self.__types__:
            raise AttributeError(name)

        if not hasattr(_cur_view, "view"):
            raise Exception("Please access properties from within a view or transaction.")

        if _cur_view.view._db is not type(self)._database:
            raise Exception("Please access properties from within a view or transaction created on the same database as the object.")
        
        return _cur_view.view._get(type(self).__name__, self._identity, name, self.__types__[name])

    def __setattr__(self, name, val):
        if self.__dict__["_identity"] == "NULL":
            raise Exception("Null object is not writeable")

        if name not in self.__types__:
            raise AttributeError(name)

        if not hasattr(_cur_view, "view"):
            raise Exception("Please access properties from within a view or transaction.")

        if _cur_view.view._db is not type(self)._database:
            raise Exception("Please access properties from within a view or transaction created on the same database as the object.")

        coerced_val = algebraic.coerce_instance(val, self.__types__[name])
        if coerced_val is None:
            raise TypeError("Can't coerce %s to %s" % (val, self.__types__[name]))

        _cur_view.view._set(type(self).__name__, self._identity, name, self.__types__[name], coerced_val)

    def clear(self):
        if self.__dict__["_identity"] is None:
            raise Exception("Null object is not writeable")

        for t in self.__types__:
            setattr(self, t, None)

    @classmethod
    def define(cls, **types):
        assert not cls.Null, "already defined"
        assert isinstance(types, dict)
        for k,v in types.iteritems():
            assert algebraic.valid_type(v)

        cls.__types__ = types
        cls.Null = cls(_creating_the_null_object)

    @classmethod
    def to_json(cls, obj):
        return obj.__dict__['_identity']

    @classmethod
    def from_json(cls, obj):
        if obj == "NULL":
            return cls.Null

        if isinstance(obj, unicode):
            obj = str(obj)

        assert isinstance(obj, str)

        return cls(obj)

class DatabaseView(object):
    def __init__(self, db, transaction_id):
        object.__init__(self)
        self._db = db
        self._transaction_num = transaction_id
        self._types = {}

    def _get(self, obj_typename, identity, field_name, type):
        key = obj_typename + ":" + identity + ":" + field_name

        db_val = self._db._get_versioned_object_data(key, self._transaction_num)

        if db_val is None:
            raise Exception("This object doesn't exist, or was deleted.")

        return _encoder.from_json(db_val, type)

    def _set(self, obj_typename, identity, field_name, type, val):
        raise Exception("Views are static. Please open a transaction")

    def _new(self, cls, kwds):
        raise Exception("Views are static. Please open a transaction to create new objects.")

    def __enter__(self):
        assert not hasattr(_cur_view, 'view')
        _cur_view.view = self
        return self

    def __exit__(self, type, val, tb):
        del _cur_view.view

class DatabaseTransaction(DatabaseView):
    def __init__(self, db, transaction_id):
        DatabaseView.__init__(self, db, transaction_id)
        self._writes = {}
        self._reads = set()

    def _new(self, cls, kwds):
        identity = sha_hash(str(uuid.uuid4())).hexdigest

        o = cls(identity)

        writes = {}

        for kwd, val in kwds.iteritems():
            if kwd not in cls.__types__:
                raise TypeError("Unknown field %s on %s" % (kwd, cls))

            coerced_val = algebraic.coerce_instance(val, cls.__types__[kwd])
            if coerced_val is None:
                raise TypeError("Can't coerce %s to %s" % (val, cls.__types__[kwd]))

            writes[cls.__name__ + ":" + identity + ":" + kwd] = coerced_val

        for tname, t in cls.__types__.iteritems():
            if tname not in kwds:
                val = algebraic.default_initialize(t)
                if val is None:
                    raise TypeError("Can't default initialize %s.%s of type %s" % (cls.__name__, tname, t))
                writes[cls.__name__ + ":" + identity + ":" + tname] = val

        self._writes.update(writes)

        return o        

    def _get(self, obj_typename, identity, field_name, type):
        key = obj_typename + ":" + identity + ":" + field_name

        if key in self._writes:
            return self._writes[key]

        self._reads.add(key)

        db_val = self._db._get_versioned_object_data(key, self._transaction_num)

        if db_val is None:
            return db_val

        return _encoder.from_json(db_val, type)

    def _set(self, obj_typename, identity, field_name, type, val):
        key = obj_typename + ":" + identity + ":" + field_name

        index_name = obj_typename + ":" + field_name
        if index_name in self._db._indices:
            cur_value = _encoder.from_json(self._db._get_versioned_object_data(key, self._transaction_num), type)
            
            old_val_hash = sha_hash(cur_value).hexdigest
            new_val_hash = sha_hash(val).hexdigest

            cur_index_list = self._db._get_versioned_object_data(index_name + ":" + old_val_hash, self._transaction_num)
            new_index_list = self._db._get_versioned_object_data(index_name + ":" + new_val_hash, self._transaction_num)

            self._writes[index_name + ":" + old_val_hash] = tuple([x for x in cur_index_list if x != identity])
            self._writes[index_name + ":" + new_val_hash] = new_index_list + (identity,)

        self._reads.discard(key)
        self._writes[key] = val

    def commit(self):
        if self._writes:
            writes = {key: _encoder.to_json(v) for key, v in self._writes.iteritems()}
            tid = self._transaction_num
            
            self._db._set_versioned_object_data(writes, tid, self._reads)

    def __enter__(self):
        assert not hasattr(_cur_view, 'view')
        _cur_view.view = self
        return self

    def __exit__(self, type, val, tb):
        del _cur_view.view
        if type is None:
            self.commit()

class Database:
    def __init__(self, kvstore):
        self._kvstore = kvstore
        self._lock = threading.Lock()
        self._cur_transaction_num = kvstore.get("transaction_id") or 1
        self._types = {}
        #type and property pairs
        self._indices = set()

    def __str__(self):
        return "Database(%s)" % id(self)

    def __repr__(self):
        return "Database(%s)" % id(self)

    def addIndex(self, type, prop):
        self._indices.add((type.__name__ + ":" + prop))

    def __getattr__(self, typename):
        if typename not in self._types:
            class cls(DatabaseObject):
                pass

            cls._database = self
            cls.__name__ = typename

            self._types[typename] = cls

        return self._types[typename]

    def view(self, transaction_id=None):
        with self._lock:
            assert transaction_id <= self._cur_transaction_num

            if transaction_id is None:
                transaction_id = self._cur_transaction_num

            return DatabaseView(self, transaction_id)

    def transaction(self):
        with self._lock:
            #no objects should have an ID greater than this number
            return DatabaseTransaction(self, self._cur_transaction_num)

    def _get_versioned_object_data(self, key, transaction_id):
        with self._lock:
            rev = self._best_revision_for_under_lock(key, transaction_id)

            if rev is None:
                return None

            return self._kvstore.get(key + ":" + str(rev))[0]

    def _best_revision_for_under_lock(self, key, transaction_id):
        cur_revision = self._kvstore.get(key + ":rev")
        if cur_revision is None:
            return None

        while True:
            if cur_revision <= transaction_id:
                return cur_revision
            else:
                cur_revision = self._kvstore.get(key + ":" + str(cur_revision))[1]
                if cur_revision is None:
                    return None

    def _set_versioned_object_data(self, key_value, transaction_id, reads):
        with self._lock:
            self._cur_transaction_num += 1
            self._kvstore.set("transaction_id", self._cur_transaction_num)

            for k in reads:
                cur = self._kvstore.get(k + ":rev")
                if cur is not None and cur > transaction_id:
                    raise RevisionConflictException()

            for k in key_value:
                cur = self._kvstore.get(k + ":rev")
                if cur is not None and cur > transaction_id:
                    raise RevisionConflictException()

            #this is the current transaction to use
            transaction_id = self._cur_transaction_num

            for k,v in key_value.iteritems():
                prior = self._kvstore.get(k + ":rev")
                self._kvstore.set(k + ":" + str(transaction_id), (v, prior))
                self._kvstore.set(k + ":rev", transaction_id)

            self._kvstore.set("transaction_" + str(transaction_id), tuple(sorted(list(key_value))))

    def _get(self, obj_typename, identity, field_name, type):
        raise Exception("Please open a transaction or a view")

    def _set(self, obj_typename, identity, field_name, type, val):
        raise Exception("Please open a transaction")


