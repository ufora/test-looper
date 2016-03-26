import redis
import json
import threading
import os


class RedisJsonStore(object):
    """Implements a string-to-json store using Redis.

    Keys must be strings. Values may be anything that's json-compatible.

    This class is thread-safe.
    """
    def __init__(self, db=0):
        self.lock = threading.Lock()
        self.redis = redis.StrictRedis(db=db)
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


class RedisJsonStoreMock(object):
    """Alternative in-memory implementation of the interface to a RedistJsonStore"""
    def __init__(self, db=0):
        self.values = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            if key not in self.values:
                return None

            return json.loads(self.values[key])

    def set(self, key, value):
        with self.lock:
            if value is None:
                if key in self.values:
                    del self.values[key]
            else:
                self.values[key] = json.dumps(value)

    def exists(self, key):
        with self.lock:
            return key in self.values

    def delete(self, key):
        with self.lock:
            if key in self.values:
                del self.values[key]

class FakeRedisFromJsonFile(object):
    def __init__(self, jsonFile='redisState.json'):
        self.store = RedisJsonStoreMock()
        directory = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(directory, jsonFile)) as data_file:
            data = json.load(data_file)
            self.testDefinitions = data['testDefinitions']
            self.testResults = data['testResults']
            self.commitIds = data['commitIds']

        self.store.set("master_test_depth", 100)

    def set(self, key, value):
        pass

    def get(self, key):
        result = self.store.get(key)
        if result is not None:
            return result
        if "commit_tests_" in key:
            return self.commitIds

        if "commit_test_definitions" in key:
            return self.testDefinitions
        if "test_" in key:
            return self.testResults
        # logging.warn("Key not found: %s" % key)
        return None
