#   Copyright 2017 Braxton Mckee
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

"""
Basic infrastructure for typed union datastructures in python
"""

from test_looper.core.hash import sha_hash

_primitive_types = (str, int, bool, float, bytes)

def valid_type(t):
    if isinstance(t, Alternative) or t in _primitive_types:
        return True

    if isinstance(t, tuple):
        for sub_t in t:
            if not valid_type(sub_t):
                return False
        return True
    
    if isinstance(t, List):
        return True

    if isinstance(t, Dict):
        return True

    if hasattr(t, "__algebraic__") and t.__algebraic__:
        return True

    return False

def coerce_instance(instance, to_type):
    if isinstance(instance, unicode):
        instance = str(instance)
    
    if isinstance(to_type, Alternative):
        if isinstance(instance, AlternativeInstance):
            if instance._alternative is to_type:
                return instance
        try:
            return to_type(instance)
        except TypeError as e:
            return None
    elif isinstance(to_type, tuple):
        if not isinstance(instance, tuple) or len(instance) != len(to_type):
            return None
        res = []
        for i in range(len(instance)):
            coerced = coerce_instance(instance[i], to_type[i])
            if coerced is None:
                return None
            res.append(coerced)
        return tuple(res)
    elif isinstance(to_type, Dict):
        if not isinstance(instance, dict):
            return None

        res = {}
        for k,v in instance.iteritems():
            res[coerce_instance(k, to_type.keytype)] = coerce_instance(v, to_type.valtype)

        return res
    elif isinstance(to_type, List):
        try:
            i = iter(instance)
        except TypeError as e:
            return None

        res = []
        while True:
            try:
                val = coerce_instance(i.next(), to_type.subtype)
                if val is None:
                    return None
                res.append(val)
            except StopIteration as e:
                return tuple(res)
    else:
        if isinstance(instance, to_type):
            return instance

def valid_fieldname(name):
    return name and name[0] != "_" and name != 'matches' and name != "define"

class Discard:
    def x(self):
        pass

boundinstancemethod = type(Discard().x)

class Alternative(object):
    def __init__(self, name, **kwds):
        object.__init__(self)
        self._name = name
        self._types = {}
        self._options = {}
        self._frozen = False
        self._methods = {}
        self._common_fields = {}
        
        for k, v in kwds.items():
            self.__setattr__(k,v)

        self._unique_field_to_type = {}
        self._types_with_unique_fields = set()

    def add_common_fields(self, fields):
        assert not self._frozen, "can't modify an Alternative once it has been frozen"
        
        for k,v in fields.items():
            self.add_common_field(k,v)

    def add_common_field(self, k, v):
        assert not self._frozen, "can't modify an Alternative once it has been frozen"

        self._common_fields[k] = v
        for tname in self._types:
            self._types[tname][k] = v

    def define(self, **kwds):
        for k,v in kwds.items():
            self.__setattr__(k,v)

    def __setattr__(self, alt_name, defs):
        if len(alt_name) >= 2 and alt_name[0] == "_" and alt_name[1] != "_":
            self.__dict__[alt_name] = defs
            return

        if isinstance(defs, type(Alternative)) and issubclass(defs, AlternativeInstance):
            defs = defs._typedict

        assert not self._frozen, "can't modify an Alternative once it has been frozen"

        assert alt_name not in self._types, "already have a definition for " + alt_name

        if isinstance(defs, dict):
            assert valid_fieldname(alt_name), "invalid alternative name: " + alt_name
        
            for fname, ftype in defs.items():
                assert valid_fieldname(fname), "%s is not a valid field name" % fname
                assert valid_type(ftype), "%s is not a valid type" % ftype

            self._types[alt_name] = dict(defs)
            self._types[alt_name].update(self._common_fields)
        else:
            self._methods[alt_name] = defs

    def _freeze(self):
        self._frozen = True
        for name, types in self._types.items():
            typenames = tuple(sorted(types.keys()))
            if typenames not in self._unique_field_to_type:
                self._unique_field_to_type[typenames] = name
                self._types_with_unique_fields.add(name)
            else:
                #multiple alternatives have exactly the same types
                existing = self._unique_field_to_type[typenames]

                if existing is not None:
                    self._types_with_unique_fields.discard(existing)

                self._unique_field_to_type[typenames] = None

    def __getattr__(self, attr):
        if attr[0] == "_":
            raise AttributeError(attr)

        if attr not in self._types:
            raise AttributeError(attr + " not a valid Alternative in %s" % sorted(self._types))

        if attr not in self._options:
            if not self._frozen:
                self._freeze()
            self._options[attr] = makeAlternativeOption(self, attr, self._types[attr], attr in self._types_with_unique_fields)

        return self._options[attr]

    def __call__(self, *args, **kwds):
        if len(self._types) == 1:
            #there's only one option - no need for any coersion
            return getattr(self, list(self._types)[0])(*args, **kwds)
        else:
            #only allow possibilities by 'arity' and name matching
            possibility = None

            if len(args) == 1 and args[0] is None:
                args = []

            if len(args) == 1:
                assert(len(kwds) == 0)
                for typename,typedict in self._types.items():
                    if len(typedict) == 1:
                        if possibility is not None:
                            raise TypeError("coersion to %s with one unnamed argument is ambiguous" % self._name)
                        possibility = typename
            else:
                assert(len(args) == 0)

                #multiple options, so it's a little ambiguous
                for typename,typedict in self._types.items():
                    if sorted(typedict) == sorted(kwds):
                        if possibility is not None:
                            raise TypeError("coersion to %s with one unnamed argument is ambiguous" % self._name)
                        possibility = typename

            if possibility is not None:
                return getattr(self,possibility)(*args, **kwds)
            else:
                raise TypeError("coersion to %s with one unnamed argument is ambiguous" % self._name)

    def __str__(self):
        return "algebraic.Alternative(%s)" % self._name

class List(object):
    def __init__(self, subtype):
        self.subtype = subtype
        assert valid_type(subtype)

class Dict(object):
    def __init__(self, keytype, valtype):
        self.keytype = keytype
        self.valtype = valtype
        assert valid_type(keytype)
        assert valid_type(valtype)

class AlternativeInstance(object):
    def __init__(self):
        object.__init__(self)

def default_initialize(tgt_type):
    if tgt_type is str:
        return str()
    if tgt_type is bool:
        return False
    if tgt_type is int:
        return 0
    if tgt_type is float:
        return 0.0
    if tgt_type is bytes:
        return bytes()
    if isinstance(tgt_type, Dict):
        return {}
    if isinstance(tgt_type, List):
        return ()
    if isinstance(tgt_type, NullableAlternative):
        return tgt_type.Null()
    if hasattr(tgt_type, "__default_initializer__"):
        return tgt_type.__default_initializer__()

    return None
    

def makeAlternativeOption(alternative, which, typedict, fields_are_unique):
    class AlternativeOption(AlternativeInstance):
        _typedict = typedict
        _fields_are_unique = fields_are_unique
        def __init__(self, *args, **fields):
            _fill_in_missing = fields.pop("_fill_in_missing", False)

            AlternativeInstance.__init__(self)

            #make sure we don't modify caller dict
            fields = dict(fields)

            if len(typedict) == 0 and len(args) == 1 and args[0] is None:
                fields = {}
            elif args:
                if len(typedict) == 1:
                    #if we have exactly one possible type, then don't need a name
                    assert not fields and len(args) == 1, "can't infer a name for more than one argument"
                    fields = {list(typedict.keys())[0]: args[0]}
                else:
                    raise TypeError("constructing %s with an extra unnamed argument" % (alternative._name + "." + which))

            for f in fields:
                if f not in typedict:
                    raise TypeError("constructing with unused argument %s: %s vs %s" % (f, fields.keys(), typedict.keys()))

            for k in typedict:
                if k not in fields:
                    if _fill_in_missing:
                        instance = default_initialize(typedict[k])
                        if instance is None:
                            raise TypeError("Can't default initialize %s" % k)
                    else:
                        raise TypeError("missing field %s" % k)
                else:
                    instance = coerce_instance(fields[k], typedict[k])
                if instance is None:
                    raise TypeError("field %s needs a %s, not %s of type %s" % (k, typedict[k], fields[k], type(fields[k])))
                fields[k] = instance

            self._fields = fields
            self._which = which
            self._alternative = alternative
            self._hash = None
            self._sha_hash_cache = None

        def __sha_hash__(self):
            if self._sha_hash_cache is None:
                self._sha_hash_cache = sha_hash(self._fields) + sha_hash(self._which)
            return self._sha_hash_cache

        def __hash__(self):
            if self._hash is None:
                self._hash = hash(self.__sha_hash__())
            return self._hash

        @property
        def matches(self):
            return AlternativeInstanceMatches(self)

        def __getattr__(self, attr):
            if attr in self._fields:
                return self._fields[attr]

            if attr in self._alternative._methods:
                return boundinstancemethod(self._alternative._methods[attr], self)

            raise AttributeError("%s not found amongst %s" % (attr, ",".join(list(self._fields) + list(self._alternative._methods))))

        def __setattr__(self, attr, val):
            if attr[:1] != "_":
                raise Exception("Field %s is read-only" % attr)
            self.__dict__[attr] = val

        def __add__(self, other):
            if '__add__' in self._alternative._methods:
                return self._alternative._methods['__add__'](self, other)
            raise TypeError("unsupported operand type(s) for +: '%s' and '%s'" % (type(self),type(other)))

        def __str__(self):
            if '__str__' in self._alternative._methods:
                return self._alternative._methods['__str__'](self)
            return repr(self)

        def __repr__(self):
            if '__repr__' in self._alternative._methods:
                return self._alternative._methods['__repr__'](self)
            return "%s.%s(%s)" % (self._alternative._name, self._which, ",".join(["%s=%s" % (k,repr(self._fields[k])) for k in sorted(self._fields)]))

        def __ne__(self, other):
            return not self.__eq__(other)

        def __eq__(self, other):
            if self is other:
                return True

            if not isinstance(other, AlternativeOption):
                return False

            if self._which != other._which:
                return False
            
            if hash(self) != hash(other):
                return False
            
            for f in sorted(self._fields):
                if getattr(self,f) != getattr(other,f):
                    return False
            
            return True

    AlternativeOption.__name__ = alternative._name + "." + which

    return AlternativeOption

class AlternativeInstanceMatches(object):
    def __init__(self, instance):
        object.__init__(self)

        self._instance = instance

    def __getattr__(self, attr):
        if self._instance._which == attr:
            return True
        return False

class NullableAlternative(Alternative):
    def __init__(self, subtype):
        self._subtype = subtype
        Alternative.__init__(self, "Nullable(" + str(subtype) + ")", Null={}, Value={'val': subtype})

_nullable_cache = {}
def Nullable(alternative):
    if alternative not in _nullable_cache:
        _nullable_cache[alternative] = NullableAlternative(alternative)

    return _nullable_cache[alternative]
