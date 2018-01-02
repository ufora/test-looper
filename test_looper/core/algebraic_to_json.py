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

import test_looper.core.algebraic as algebraic

class Encoder:
    """An algebraic <---> json encoder.

    The encoding is:
        * primitive types (str, int, bool, float) are encoded directly
        * Alternatives are encoded as objects. If the field list identifies the object uniquely, 
            the object just contains the fields. Otherwise a "type" field is introduced  containing
            the name of the type.
        * Alternatives that have no fields may be encoded as a string giving the name of the
            alternative.
        * Lists are encoded as arrays
        * Nullables are encoded as None or the object.
    """    
    def to_json(self, value):
        if isinstance(value, unicode):
            value = str(value)

        if isinstance(value, algebraic.AlternativeInstance):
            if isinstance(value._alternative, algebraic.NullableAlternative):
                if value.matches.Null:
                    return None
                return self.to_json(value.val)
            elif not value._fields:
                return value._which
            else:
                assert "_type" not in value._fields

                json = {}
                if not value._fields_are_unique:
                    json["_type"] = value._which

                for fieldname, val in value._fields.items():
                    json[fieldname] = self.to_json(val)
                
                return json

        elif isinstance(value, (list, tuple)):
            return [self.to_json(x) for x in value]

        elif isinstance(value, dict):
            return {self.to_json(k): self.to_json(v) for k,v in value.iteritems()}

        elif isinstance(value, algebraic._primitive_types):
            return value
        elif hasattr(type(value), "to_json"):
            return type(value).to_json(value)
        elif value is None:
            return value
        else:
            assert False, "Can't convert %s" % (value,)

    def from_json(self, value, algebraic_type):
        if isinstance(value, unicode):
            value = str(value)

        if value is None:
            return value

        if isinstance(algebraic_type, algebraic.NullableAlternative):
            if value is None:
                return None
            return algebraic_type.Value(val=self.from_json(value, algebraic_type._subtype))

        if isinstance(algebraic_type, tuple):
            value = list(value)

            assert len(algebraic_type) == len(value), "Can't convert %s to %s" % (value, algebraic_type)
            return tuple([self.from_json(value[x], algebraic_type[x]) for x in xrange(len(value))])

        if isinstance(algebraic_type, algebraic.Dict):
            return {self.from_json(k, algebraic_type.keytype):self.from_json(v, algebraic_type.valtype) for k,v in value.iteritems()}

        if isinstance(algebraic_type, algebraic.List):
            #allow objects to be treated as lists of tuples
            if isinstance(value, dict):
                value = value.items()

            return [self.from_json(v, algebraic_type.subtype) for v in value]

        if algebraic_type in algebraic._primitive_types:
            return value

        if isinstance(algebraic_type, algebraic.Alternative):
            if isinstance(value, unicode):
                value = str(value)

            if isinstance(value, str):
                assert hasattr(algebraic_type, value), "Algebraic type %s has no subtype %s" % (algebraic_type, value)
                return getattr(algebraic_type, value)()
            else:
                assert isinstance(value, dict)

                if '_type' in value:
                    if isinstance(value['_type'], unicode):
                        value['_type'] = str(value['_type'])
                    
                    if not isinstance(value['_type'], str):
                        raise UserWarning('typenames have to be strings')

                    if not hasattr(algebraic_type, value['_type']):
                        raise UserWarning(
                            "Can't find type %s in %s" % (value['type'], algebraic_type)
                            )

                    which_alternative = getattr(algebraic_type, value['_type'])
                else:
                    possible = list(algebraic_type._types)
                    for fname in value:
                        possible = [p for p in possible if fname in algebraic_type._types[p]]
                        if not possible:
                            raise UserWarning("Can't find a type with fieldnames " + str(sorted(value)))


                    if len(possible) > 1:
                        possible = [p for p in possible if len(algebraic_type._types[p]) == len(value)]
                    
                    if len(possible) > 1:
                        raise UserWarning("Type is ambiguous: %s could be any of %s" % (sorted(value), possible))

                    which_alternative = getattr(algebraic_type, possible[0])

                subs = dict([(k, self.from_json(value[k], which_alternative._typedict[k])) 
                                for k in value if k != '_type'])

                try:
                    return which_alternative(_fill_in_missing=True, **subs)
                except:
                    raise
        
        if hasattr(algebraic_type, "from_json"):
            return algebraic_type.from_json(value)

        assert False, "Can't handle type %s as value %s" % (algebraic_type,value)

