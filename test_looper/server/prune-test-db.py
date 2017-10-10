#!/usr/bin/env python

from test_looper.core.tools.Git import Git
from test_looper.server.RedisJsonStore import RedisJsonStore
from test_looper.server.TestManager import TestDatabase

git = Git()

git_commits = set()
for branch in git.listBranches():
    git_commits.update(c[0] for c in git.commitsInRevList(branch))

redis = RedisJsonStore()
test_db = TestDatabase(redis)

def delete_commits_by_prefix(prefix):
    prefix_len = len(prefix)
    db_commits = set(key[prefix_len:] for key in redis.keys(prefix + "*"))

    deleted_count = 0
    for commit in db_commits - git_commits:
        deleted_count += 1
        test_db.clearAllTestsForCommitId(commit)
        redis.delete(prefix + commit)
    return deleted_count

total_deleted = sum(delete_commits_by_prefix(p)
                    for p in ('commit_test_definitions_', test_db.dbPrefix + 'commit_test_'))

print "Deleted tests for %d commits" % total_deleted
