#test-looper - The statistical CI

Test-looper is a framework for testing code in GIT repositories. Tests can
be run multiple times for any given commit, and for all commits in a branch,
so that we can hunt down hard-to-find periodic test failures. The tests and build
behaviors are defined within the repo itself, and tests are run in a docker image,
which allows us to test how changes to the environment we're running in affect
our code.

Every repo should provide a "testDefinitions.json" file in a consistent place in
the repo. The file should parse to a single object with the following fields:

testDefinitions.json:
    dockerfile: an object containing one of 
        tag: name of an image to use
        dockerfile: path (relative to REPO_DIR) to a dockerfile to be used for all tests
    build:
        command:
            the command to be run (by a shell) from the REPO_DIR to run the test
            should do a build and place build artifacts in BUILD_DIR
            logs should be placed in OUTPUT_DIR
    tests: a list of test objects, each having:
        name: the name of the test suite. "::" can be used to place tests into groups and subgoups
        command: the shell command to run from the REPO dir to run the test.
            test outputs should be placed in OUTPUT_DIR

Every test that we run has the following environment variables defined:
    REVISION: the current commit
    TEST_SRC_DIR: path to a copy of the codebase. Guaranteed to be a clean copy that you can write into. Doesn't have a copy of .git
    TEST_BUILD_DIR: 
        Directory into which the build artifacts should be placed by the build step. 
        These will be tarballed and stored. They will be untarballed and available for use by tests.
    TEST_OUTPUT_DIR: 
        Directory where test outputs and logs should go.
        Text files will be tarballed and gzipped.
        Other files will be uploaded as-is.
        Directories will be ignored.
    TEST_CCACHE_DIR: 
        A persistent cache directory for use by 'ccache'
    TEST_LOOPER_TEST_ID: a unique identifier for the current test run

Tests are run in a dockerfile. All test runs have the docker socket mounted
so that the container can boot sister containers. The docker socket is monitored
by the test-looper to ensure that the docker containers can't conflict with each other.
By default each tester runs in its own subnet and prefixes the names of containers with
a unique ID to prevent two test containers from conflicting with each other.
