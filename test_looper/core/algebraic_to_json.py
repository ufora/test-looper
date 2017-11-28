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
    """An algebraic <---> json encoder"""
    
    def to_json(self, value):
        if isinstance(value, algebraic.AlternativeInstance):
            if isinstance(value._alternative, algebraic.NullableAlternative):
                if value.matches.Null:
                    return None
                return self.to_json(value.val)
            else:
                json = {}
                json['type'] = value._which
                for fieldname, val in value._fields.items():
                    json[fieldname] = self.to_json(val)
                return json

        elif isinstance(value, (list, tuple)):
            return [self.to_json(x) for x in value]

        elif isinstance(value, algebraic._primitive_types):
            return value
        else:
            assert False, "Can't convert %s" % (value,)

    def from_json(self, value, algebraic_type):
        if isinstance(algebraic_type, algebraic.NullableAlternative):
            if value is None:
                return None
            return algebraic_type.Value(val=self.from_json(value, algebraic_type._subtype))

        if isinstance(algebraic_type, algebraic.List):
            #allow objects to be treated as lists of tuples
            if isinstance(value, dict):
                value = value.items()

            return [self.from_json(v, algebraic_type.subtype) for v in value]

        if algebraic_type in algebraic._primitive_types:
            return value

        if isinstance(algebraic_type, algebraic.Alternative):
            assert isinstance(value, dict)

            if 'type' not in value:
                raise UserWarning(
                    "Can't construct a %s without a given 'type' field" % algebraic_type
                    )
            if not isinstance(value['type'], str):
                raise UserWarning('typenames have to be strings')

            if not hasattr(algebraic_type, value['type']):
                raise UserWarning(
                    "Can't find type %s in %s" % (value['type'], algebraic_type)
                    )

            which_alternative = getattr(algebraic_type, value['type'])

            subs = dict([(k, self.from_json(value[k], which_alternative._typedict[k])) 
                            for k in value if k != 'type'])

            return which_alternative(**subs)

        assert False, "Can't handle type %s" % algebraic_type

