from test_looper.core.hash import sha_hash

_index_to_char = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/"
assert len(_index_to_char) == 64
_char_to_index = {_index_to_char[i]: i for i in range(len(_index_to_char))}


def _packBitstringToInt(bools):
    """Given an array of up to 6 bools, pack them into an 6-bit integer, first bool as least significant bit."""
    res = 0
    bit = 1
    for i in range(len(bools)):
        if bools[i]:
            res += bit
        bit *= 2
    return res


class Bitstring(object):
    """Models a bunch of bools packed into a base64-encoded string."""

    __algebraic__ = True

    def __init__(self, bits):
        object.__init__(self)

        self.bits = bits

    @staticmethod
    def fromBools(bools):
        """Given an array of bools, pack them into a string as efficiently as possible (e.g. one bit at a time)"""
        s = []
        i = 0
        while i < len(bools):
            s.append(_index_to_char[_packBitstringToInt(bools[i : i + 6])])
            i += 6
        return Bitstring("".join(s))

    def __getitem__(self, ix):
        if int(ix / 6) >= len(self.bits):
            return False

        return (_char_to_index[self.bits[int(ix / 6)]] & (1 << (ix % 6))) > 0

    @classmethod
    def to_json(cls, obj):
        return obj.bits

    @classmethod
    def from_json(cls, obj):
        return Bitstring(str(obj))

    def __sha_hash__(self):
        return sha_hash(self.bits)

    @classmethod
    def __default_initializer__(cls):
        return Bitstring("")
