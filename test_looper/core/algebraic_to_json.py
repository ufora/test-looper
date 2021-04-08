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
import logging
import json
import yaml
import re


def setupYamlLoadersAndDumpers():
    try:
        # Use the native code backends, if available.
        from yaml import CSafeLoader as Loader, CDumper as Dumper
    except ImportError:
        from yaml import SafeLoader as Loader, Dumper

    def string_representer(dumper, value):
        style = None

        # If it has newlines, request a block style.
        if "\n" in value:
            style = "|"

        # if it looks like an identifier, use no style
        if (
            re.match(r"^[a-zA-Z0-9_\-/]+$", value)
            and len(value) < 60
            and value not in ("true", "false")
        ):
            style = ""

        return dumper.represent_scalar(u"tag:yaml.org,2002:str", value, style=style)

    Dumper.add_representer(str, string_representer)
    Dumper.add_representer(
        int,
        lambda dumper, value: dumper.represent_scalar(
            u"tag:yaml.org,2002:int", str(value), style=""
        ),
    )
    Dumper.add_representer(
        bool,
        lambda dumper, value: dumper.represent_scalar(
            u"tag:yaml.org,2002:bool", u"true" if value else u"false", style=""
        ),
    )
    Dumper.add_representer(
        type(None),
        lambda dumper, value: dumper.represent_scalar(u"tag:yaml.org,2002:null", u"~"),
    )

    def construct_tuple(loader, node):
        return tuple(Loader.construct_sequence(loader, node))

    Loader.add_constructor(u"tag:yaml.org,2002:seq", construct_tuple)


setupYamlLoadersAndDumpers()


def flattenListsAndTuples(toFlatten):
    if not isinstance(toFlatten, (tuple, list)):
        return toFlatten
    res = []
    for item in toFlatten:
        if isinstance(item, (tuple, list)):
            res.extend(flattenListsAndTuples(item))
        else:
            res.append(item)
    return res


class Encoder(object):
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

    if mergeListsIntoDicts is true, then when parsing a dict, we iterate over json elements
        that are not dicts and parsed them as if they are collections of dicts. This can
        be useful when parsing large yaml files. If false, then we insist that the json
        representation of a dict is a dict or a list of tuples.
    """

    def __init__(self, mergeListsIntoDicts=True):
        object.__init__(self)
        self.mergeListsIntoDicts = mergeListsIntoDicts
        self.overrides = {}

        # if True, then we ignore extra fields that don't correspond to valid fields
        self.allowExtraFields = False

    def to_json(self, value):
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
            return {self.to_json(k): self.to_json(v) for k, v in value.items()}

        elif isinstance(value, algebraic._primitive_types):
            return value
        elif hasattr(type(value), "to_json"):
            return type(value).to_json(value)
        elif value is None:
            return value
        else:
            assert False, "Can't convert %s" % (value,)

    def from_json(self, value, algebraic_type):
        if algebraic_type in self.overrides:
            return self.overrides[algebraic_type](self, value)

        try:
            if value is None:
                if isinstance(algebraic_type, algebraic.Dict):
                    return {}
                else:
                    return value

            if isinstance(algebraic_type, algebraic.NullableAlternative):
                if value is None:
                    return None
                return algebraic_type.Value(
                    val=self.from_json(value, algebraic_type._subtype)
                )

            if isinstance(algebraic_type, tuple):
                value = list(value)

                assert len(algebraic_type) == len(value), "Can't convert %s to %s" % (
                    value,
                    algebraic_type,
                )
                return tuple(
                    [
                        self.from_json(value[x], algebraic_type[x])
                        for x in range(len(value))
                    ]
                )

            if isinstance(algebraic_type, algebraic.Dict):
                if isinstance(value, dict):
                    return {
                        self.from_json(k, algebraic_type.keytype): self.from_json(
                            v, algebraic_type.valtype
                        )
                        for k, v in value.items()
                    }

                if self.mergeListsIntoDicts:
                    if not isinstance(value, (list, tuple)):
                        raise UserWarning(
                            "Can't convert %s to a %s" % (value, algebraic_type)
                        )
                    res = {}
                    for subitem in value:
                        for k, v in self.from_json(subitem, algebraic_type).items():
                            res[k] = v
                    return res
                else:
                    return {
                        self.from_json(k, algebraic_type.keytype): self.from_json(
                            v, algebraic_type.valtype
                        )
                        for k, v in value
                    }

            if isinstance(algebraic_type, algebraic.List):
                # first, perform implicit flattening of lists and tuples if the sub-item is not itself a list
                if not isinstance(algebraic_type.subtype, algebraic.List):
                    value = flattenListsAndTuples(value)

                # allow objects to be treated as lists of tuples
                if isinstance(value, dict):
                    value = value.items()
                if isinstance(value, (str, int, bool)):
                    value = (value,)
                return tuple(self.from_json(v, algebraic_type.subtype) for v in value)

            if algebraic_type in algebraic._primitive_types:
                if algebraic_type is str and isinstance(value, bool):
                    value = "true" if value else "false"
                if algebraic_type is str and isinstance(value, int):
                    value = str(value)
                if algebraic_type is float and isinstance(value, int):
                    value = float(value)

                return value

            if isinstance(algebraic_type, algebraic.Alternative):
                if isinstance(value, str):
                    zero_arg_types = []
                    single_arg_types = []
                    for t in algebraic_type._types:
                        if len(algebraic_type._types[t]) == 0:
                            zero_arg_types.append(t)
                        if (
                            len(algebraic_type._types[t]) == 1
                            and list(algebraic_type._types[t].values())[0] is str
                        ):
                            single_arg_types.append(t)

                    if len(single_arg_types) == 1 and not zero_arg_types:
                        # there's exactly one type that takes a single string
                        which_alternative = getattr(algebraic_type, single_arg_types[0])
                        return which_alternative(value)

                    assert hasattr(
                        algebraic_type, value
                    ), "Algebraic type %s has no subtype %s" % (algebraic_type, value)
                    return getattr(algebraic_type, value)()
                else:
                    assert isinstance(value, dict)

                    if "_type" in value:
                        if not isinstance(value["_type"], str):
                            raise UserWarning("typenames have to be strings")

                        if not hasattr(algebraic_type, value["_type"]):
                            raise UserWarning(
                                "Can't find type %s in %s"
                                % (value["type"], algebraic_type)
                            )

                        which_alternative = getattr(algebraic_type, value["_type"])
                    else:
                        possible = list(algebraic_type._types)
                        for fname in value:
                            # check to see which types are still possible with this field in play
                            possible_here = [
                                p for p in possible if fname in algebraic_type._types[p]
                            ]

                            if self.allowExtraFields:
                                # if we allow extra fields (say, from a legacy database entry) and that
                                # would rule _everything_ out, we can ignore it
                                if possible_here:
                                    possible = possible_here
                            else:
                                possible = possible_here
                                if not possible:
                                    raise UserWarning(
                                        "Can't find a type with fieldnames "
                                        + str(sorted(value))
                                    )

                        if len(possible) > 1:
                            # pick the smallest one. If it's unique, thats OK.
                            smallestCount = min(
                                [len(algebraic_type._types[p]) for p in possible]
                            )
                            possible = [
                                p
                                for p in possible
                                if len(algebraic_type._types[p]) == smallestCount
                            ]

                        if len(possible) > 1:
                            raise UserWarning(
                                "Type is ambiguous: %s could be any of %s"
                                % (sorted(value), possible)
                            )

                        which_alternative = getattr(algebraic_type, possible[0])

                    subs = dict(
                        [
                            (
                                k,
                                self.from_json(
                                    value[k], which_alternative._typedict[k]
                                ),
                            )
                            for k in value
                            if k != "_type"
                            if k in which_alternative._typedict
                        ]
                    )

                    try:
                        return which_alternative(
                            _fill_in_missing=True,
                            _allow_extra=self.allowExtraFields,
                            **subs
                        )
                    except:
                        raise

            if hasattr(algebraic_type, "from_json"):
                return algebraic_type.from_json(value)

            assert False, "Can't handle type %s as value %s" % (algebraic_type, value)
        except:
            logging.error(
                "Parsing error making %s:\n%s",
                algebraic_type,
                json.dumps(value, indent=2),
            )
            raise


def encode_and_dump_as_yaml(value):
    return yaml.dump(Encoder().to_json(value), indent=4, default_style='"')
