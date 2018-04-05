# TestLooper - The statistical CI

TestLooper is a framework for testing code in GIT repositories. Tests can
be run multiple times for any given commit, and for all commits in a branch,
so that we can hunt down hard-to-find periodic test failures. 

TestLooper follows a few core principles:

* The semantic behavior of a test run should be contained entirely within the contents
  of source control, including details of the software environment in which the test
  is run. Configuration of TestLooper determines which tests to run and with what
  priority, but not what they mean.
* All dependencies on external software should be fully pinned, so that they can't change. 
  For packages, this means full version numbers. For installation media or test data in external data stores, 
  this means they have a sha-hash of the file contents in the file name.
* We need to see test runs over the history of a branch, not just on the top commit.
  The more complex the software system, the more likely we have non-deterministic 
  test failures. These are often indicative of subtle bugs that can cause infrequent
  but hard-to-fix failures. 

By following these principles, TestLooper can help you understand how each individual 
commit is affecting the quality of a software project so you can get rid of 
hard-to-find bugs that only seem to surface at the worst possible moment.

Detailed documentation can be found here:

* [format for test definitions files](https://github.com/ufora/test-looper/blob/master/docs/testDefinitions.md)
* [using tl.py](https://github.com/ufora/test-looper/blob/master/docs/tl.md)
* [test-looper server configuration and administration](https://github.com/ufora/test-looper/blob/master/docs/serverConfig.md)

### Features

TestLooper has a few valuable features that make it useful for complex projects:

* Supports testing in both Windows and Linux, on either naked VMs in AWS or in docker.
* Commandline tooling to replicate the cloud environment on your desktop as closely as possible (tl.py)
* Deep dependency tracking, so that builds and tests with the same exact inputs defined in different
commits are shared.
* Supports complex dependency graphs of tests and builds that can span multiple repos and commits.
* Parallelizes complex build and test recipes across multiple machines and manages dependencies
* Manages booting machines in AWS and installing software dependencies
* Allows tests to define their resource requirements, (enabling a big parallel C++ build on a 32 core box,
which a bunch of little 1-core test processes can consume)
* Works with a variety of git services, including gitlab and github
* Supports testing a single commit or build in many different environments
* Exposes a "scoped" docker to tests running within docker containers
* Support for cross-repo branch pins to help manage complex build dependency graphs
* Live logs from tests, and "interactive deployments" that give you an ssh terminal
    in your browser into any test environment defined in your source tree.

### Deployment

The TestLooper webserver can be run on a single linux server with docker (in
which case only docker linux builds are available) or on a linux server with
AWS credentials. Test and build artifacts can be stored on local disks of the
server or in amazon S3.  Git repos can be on local disk, gitlab, or github
(enterprise or web). The live state of the  system (what tests are running,
list of commits, etc) is stored in a redis database. The state of test-
success/failure can be replicated from the build/test artifacts, however, so
the redis database can be wiped and repopulated.

# TestLooper conceptual model

## Object model

TestLooper must be connected to some form of git server, which defines a set of 
repos. Each repo has a name, and a set of branches, and each branch has a set
of commits. 

Within each commit, TestLooper looks for a file whose name ends with
'.testlooper.yml' or 'testDefinitions.yml'. This yml file defines a set
external repos, environments, builds, tests, and deployments. Each such object
is named a unique name within the definition file, giving it a global name of
"repo/commit_hash/name". The yaml file may include content from other text
files contained within the repo or any referenced repo.

*Repos* define references to other commits on the git server. Such references
must be fully qualified as a reponame and a full sha-hash. This ensures that
the contents of the repo pin are always the same.  Optionally, a repo reference
may be pinned to a specific branch in another repo, in which case TestLooper
TestLooper will push additional commits updating the pinned reference to the 
top of the referenced branch whenever it changes.

*Environments* define the software environment in which a test runs. An environment
consists of
* a platform (linux or windows)
* a base image (either an AMI for a naked windows VM running in AWS, or a dockerfile)
* environment variables
* image setup commands (to install software dependencies)
* build or source artifacts from other repos to expose as dependencies
Environments have an inheritance model, allowing us to import them from
other repos and to compose them together to install different kinds of software
or environment variables.

*Builds* define processes that produce binary output for other processes to
consume. Often they are the result of compiling code, but they can also pull
resources from external system. Builds may depend on the source tree or on any
other builds defined in any of the Repos referred to in the commit. Builds may
be configured to 'retry' some number of times if they depend on external
processes that can fail sporadically, (or which produce output that shows up at
some future, undetermined time).

Each build specifies the environment it runs on, a set of additional build and
source dependencies (which consist of a repo and an optional subdirectory
within that repo), a set of environment variables, a commandline to run
(powershell in windows, bash in linux), and a cleanup command that gathers
artifacts.  The hash of all of these inputs defines a 'build hash'. Any two
builds across the system that have identical hashes are considered the same
object. As a result, if two commits define an identical set of builds, each
build only gets executed once.

TestLooper assembles the dependent build dependencies (if any of its
dependencies fail, the build is considered "blocked on failed builds"), as
well as any source repos that are depended on, and exposes them in a directory
specified by the "TEST_INPUTS" environment variable.

TestLooper then executes the specified commandline script and collects the
contents of the the "TEST_BUILD_OUTPUT_DIR" directory (exposed as an
environment variable) which defines the result of the build. If the
commandline exits with a nonzero exit code, the build is considered 'failed'.

If a build fails, it may be retriggered by a user until it succceeds, at which
point dependent processes will continue.

*Tests* consume builds and source trees in the same manner as builds, but
produce a pass/fail result, a manifest of individual test cases that may have
passed or failed, and a set of "Test Artifacts" (which must be placed in the
directory specified by the TEST_OUTPUT_DIR environment variable). Unlike
Builds, tests may be run multiple times so we can inspect their failure rate.

*Deployments* are configured like builds or tests, but don't run unless
explicitly 'booted' by the user, in which case they are scheduled, hardware is
booted, and the "deployment command" is run. However, once the command
finishes, the system is left running until it is shut down. Users may
"connect" to the deployment using a web-based terminal, make changes, inspect
state, etc.

Every build, test, and deployment can expose a 'project' and 'configuration'
definition. These are strings that are used by the TestLooper front end to
group tests 

## Resource management and prioritization.

The TestLooper object model defines a set of tests and builds, given a set of
branches and commits. By default, no tests or builds are executed, since there
may be a very large number of them. Each commit has an individual flag
indicating whether it is enabled for testing, and each branch has a flag 
indicating that the top of the branch should be enabled for testing, 
as well as all new commits on the branch.

TestLooper examines the set of all commits enabled for testing and propagates
dependencies down through dependent tests. As a result, if test `repo1/commit1/T`
depends on build `repo2/commit2/B`, and that build depends on `repo3/commit3/B2`,
TestLooper will ensure that all build dependencies are built and run in the
correct order.

TestLooper will boot hardware resources in AWS to meet the requirements of
the scheduled tests. TestLooper can be configured with limits for the number
of machines, cores, or GB of ram it will boot at any given time. Booted machines
execute tests as long as they are available. If they are idle for a long time,
we shut them down. Machines that run tests that specify a naked AMI 
get shut down after every test or deployment, since the state of the system
can't be rolled back.

TestLooper will cancel running tests or builds if a test or build priority
goes to zero.  In particular, TestLooper always deprioritizes any commit
that becomes unreachable (for instance, if a branch is deleted, or force-pushed
with an alternative history).  Such "orphaned" commits may still be referenced
by the other commits in hard sha-hashes. If this is a common use-case for you,
it's important to make sure your main Git repository doesn't purge these
lost commits by setting the git garbage collection policy appropriately.

## The `tl.py` commandline front-end

TestLooper ships with a front-end python script called `tl.py` that can checkout
and build TestLooper projects. `tl.py` will check out multiple repositories
on your behalf, manage their state, parse the testDefinition files so you can
tell what tests have been defined, and will run builds and tests on your 
local machine.  Because the repos are checked out locally, you can make
modifications and see how they affect your build and test runs, and if you are
using a build tool such as make, scons, or bazel, not have to completely 
rebuild the source tree.

On linux, `tl.py` uses docker to build and run tests, and can (almost) exactly
replicate the tests environment used in the real looper. On windows, (because
we don't yet support a full docker-style environment), `tl.py` ignores the 
details of machine images and runs commands nakedly against your machine.
This means that local environment changes (say, to your registry because
you have build tools installed) can affect the results.

# Testing TestLooper

TestLooper has a set of tests in the "test_looper_tests" folder.

    nosetests test_looper_tests

should work to execute these. We expect to be running in linux with
docker available for the tests.

You can also run a simple in-memory version of the http server testing
all local contents by going to `test_looper_tests/system_test/` and running
`run_system.sh`, which will create some git repos and boot up the server
on port 9081.

# What's left to do

TestLooper is still under active development. In particular, the major
areas I'd like to invest in are:

* Fix our authentication model.
* Extend docker support to Windows, which would let us get the same granularity 
benefits as we have on linux, and would let us parallelize windows tests more. It
would also let us re-use windows boxes in AWS across tests.
* Support for caching setup commands as AMIs or docker images. Right now
we rebuild the machine image from scratch which is expensive, and dangerous
since external package management frameworks could change. Instead, we could
build an AMI or a docker image (stored in a private docker repository) for 
each unique setup command as a separate step.
* Improve the remote terminal experience on powershell. Right now, Windows
"terminal deployments" are somewhat hard to work with because our front-end
speaks standard VT100 terminal escape codes which Windows terminals don't
know how to produce. As a result, command-line editing is broken - you basically
have to edit in another text editor and paste in. We could fix this a number
of ways.
* Upgrade our HTTP front end to angular so we can see things as they're changing
* Better support for managing pins, branches, and orphaned commits directly in 
TestLooper.
