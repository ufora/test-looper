import redis
import json
import threading
import os


class RedisJsonStore(object):
    """Implements a string-to-json store using Redis.

    Keys must be strings. Values may be anything that's json-compatible.

    This class is thread-safe.
    """
    def __init__(self, db=0, port=None):
        self.lock = threading.Lock()
        kwds = {}
        
        if port is not None:
            kwds['port'] = port

        self.redis = redis.StrictRedis(db=db, **kwds)
        self.cache = {}

    def get(self, key):
        with self.lock:
            if key in self.cache:
                return self.cache[key]

            result = self.redis.get(key)

            if result is None:
                return result

            result = json.loads(result)

            self.cache[key] = result

            return result

    def set(self, key, value):
        with self.lock:
            if value is None:
                self.redis.delete(key)
                if key in self.cache:
                    del self.cache[key]
            else:
                self.cache[key] = value
                self.redis.set(key, json.dumps(value))

    def exists(self, key):
        with self.lock:
            if key in self.cache:
                return True
            return self.redis.exists(key)

    def delete(self, key):
        with self.lock:
            if key in self.cache:
                del self.cache[key]
            self.redis.delete(key)
