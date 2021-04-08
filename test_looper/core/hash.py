import hashlib
import struct

class Hash(object):
    def __init__(self, digest):
        self.digest = digest

    @staticmethod
    def from_integer(i):
        return Hash.from_string(struct.pack("!q", i))

    @staticmethod
    def from_float(f):
        return Hash.from_string(struct.pack("!d", f))

    @staticmethod
    def from_string(string):
        hasher = hashlib.sha1()
        hasher.update(string.encode('utf8') if isinstance(string, str) else string)
        return Hash(hasher.digest())

    def __add__(self, other):
        assert isinstance(other, Hash)
        hasher = hashlib.sha1()
        hasher.update(self.digest)
        hasher.update(other.digest)
        return Hash(hasher.digest())

    @property
    def hexdigest(self):
        return self.digest.hex()

    def __str__(self):
        return "0x" + self.hexdigest

    def __repr__(self):
        return "0x" + self.hexdigest

    def __hash__(self):
        return hash(self.digest)

    def __eq__(self, other):
        return self.digest == other.digest


def sha_hash(val):
    if isinstance(val, tuple):
        h0 = Hash.from_integer(len(val))
        for i in val:
            h0 = h0 + sha_hash(i)
        return h0
    if isinstance(val, dict):
        return sha_hash(tuple(sorted(val.items())))
    if isinstance(val, int):
        return Hash.from_integer(val)
    if isinstance(val, float):
        return Hash.from_float(val)
    if isinstance(val, str):
        return Hash.from_string(val)
    return val.__sha_hash__()
