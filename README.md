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

### Features

TestLooper has a few valuable features that make it useful for complex projects:

* Supports testing in both Windows and Linux, on either naked VMs in AWS or in docker.
* Supports complex dependency graphs of tests and builds that can span multiple repos.
* Parallelizes complex build and test recipes across multiple machines and manages dependencies
* Manages booting machines in AWS and installing software dependencies
* Allows tests to define their resource requirements, (enabling a big parallel C++ build on a 32 core box,
which a bunch of little 1-core test processes can consume)
* Can work with a variety of git services, including gitlab and github
* Supports testing a single commit or build in many different environments
* Exposes a "scoped" docker to tests running within docker containers
* Support for cross-repo branch pins to help manage complex build dependency graphs
* Live logs from tests, and "interactive deployments" that give you an ssh terminal
    in your browser into any test environment defined in your source tree.

### Deployment

TestLooper can be run on a single linux server with docker (in which case only docker 
linux builds are available) or on a linux server with AWS credentials. Test and
build artifacts can be stored on local disks of the server or in amazon S3.  Git repos can
be on local disk, gitlab, or github (enterprise or web). The live state of the 
system (what tests are running, list of commits, etc) is stored in a redis database.
The state of test-success/failure can be replicated from the build/test artifacts,
however, so the redis database can be wiped and repopulated.

# TestLooper conceptual model

## Object model

TestLooper must be connected to some form of git server, which defines a set of 
repos. Each repo has a name, and a set of branches, and each branch has a set
of commits. 

Within each commit, test-looper looks for a 'testDefinitions.yml' file
which defines a set external repos, environments, builds, tests, and deployments. Each
such object is named a unique name within the definition file, giving it a global
name of "repo/commit_hash/name".

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

*Builds* define processes that produce binary output for other processes to consume.
Often they are the result of compiling code, but they can also pull resources from
external system. Builds may depend on the source tree or on any other builds defined
in any of the Repos referred to in the commit. Builds
may be configured to 'retry' some number of times if they depend on external
processes. 

Each build specifies the environment it runs on,
a set of additional build and source dependencies, a commandline to run (powershell
in windows, bash in linux), and a cleanup command that gathers artifacts.

TestLooper assembles the dependent build dependencies (if any of its dependencies fail, the
build is considered "blocked on failed builds), as well as any source repos
that are depended on, and exposes them in a directory specified by the
"TEST_INPUTS" environment variable.  

TestLooper then executes the specified commandline script and  collects the
contents of the the "TEST_BUILD_OUTPUT_DIR" directory (exposed as an
environment variable) which defines the result of the build. If the
commandline exits with a nonzero exit code, the build is considered 'failed'.

If a build fails, it may be re-run until it succceeds, at point dependent
processes will continue.

*Tests* consume builds and source trees in the same manner as builds, but 
produce a pass/fail result, a manifest of individual test cases that may have passed or failed,
and a set of "Test Artifacts" (which must be placed in the directory specified by the
TEST_OUTPUT_DIR environment variable). Unlike Builds, tests may be run multiple times
so we can inspect their failure rate.

*Deployments* are configured like builds or tests, but don't run unless
explicitly 'booted' by the user, in which case they are scheduled, hardware
is booted, and the "deployment command" is run. However, once the command finishes,
the system is left running until it is shut down. Users may "connect" to the deployment
using a web-based terminal, make changes, inspect state, etc.

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

# TestLooper setup and administration

## Dependencies

TestLooper is written in python2.7, and depends on git, docker, and some
python packages.

It's enough to execute the following on the blank ubuntu:16.04 docker image:

    apt-get -y update
    apt-get -y install git
    apt-get -y install python python-pip
    apt-get -y install curl
    apt-get -y install redis-server
    apt-get -y install zip
    pip install pyyaml cherrypy ws4py pyOpenSSL psutil simplejson requests
    pip install docker redis boto3 markdown

Make sure you expose -v /var/run/docker.sock:/var/run/docker.sock if you want to run
inside docker. Or you can install these dependencies yourself.

If you don't have docker, you can find instructions for installing it 
[here](https://docs.docker.com/install/linux/docker-ce/ubuntu/#set-up-the-repository).

TestLooper runs as a daemon. You can run the server yourself in a terminal, or use
the provided scripts in "deploy". As it currently stands, the default configurations
in 'deploy' assume you are running commands from 'deploy' sitting inside of the
test-looper source. 

## Starting/Stopping the system

From the 'deploy' directory, modify 'config.json' to your taste and then run

    ./redis_ctl.sh start
    ./looper_ctl.sh start

which will boot daemons for both redis and test-looper-server.

All configuration is translated directly into objects in `test_looper/core/Config.py`,
so see that file for all the details. 

If you change the configuration, restart the looper service. Running tests
shouldn't be affected - they'll wait indefinitely until they can connect back to 
the service.

## Github/Gitlab configuration

Within the config.json file is a section called 'source_control'. This
may be configured as:

    "source_control": { "path_to_repos": "..." }

to specify that you're using local repos (this wont work with AWS),

    "source_control": {
        "private_token": "$GITLAB_PRIVATE_TOKEN",
        "auth_disabled": true,
        "oauth_key": "$GITLAB_OAUTH_KEY",
        "oauth_secret": "$GITLAB_OAUTH_SECRET",
        "webhook_secret": "$GITLAB_WEBHOOK_SECRET",
        "group": "...",
        "gitlab_url": "https://gitlab.COMPANYNAME.com",
        "gitlab_login_url": "https://gitlab.COMPANYNAME.com",
        "gitlab_api_url": "https://gitlab.COMPANYNAME.com/api/v3",
        "gitlab_clone_url": "git@gitlab.COMPANYNAME.com"
        }

for gitlab, or 

    "source_control": {
        "access_token": "$GITHUB_ACCESS_TOKEN",
        "auth_disabled": true,
        "oauth_key": "$GITHUB_OAUTH_KEY",
        "oauth_secret": "$GITHUB_OAUTH_SECRET",
        "webhook_secret": "$GITHUB_WEBHOOK_SECRET",
        "owner": "...",
        "github_url": "https://github.COMPANYNAME.com",
        "github_login_url": "https://github.COMPANYNAME.com",
        "github_api_url": "https://github.COMPANYNAME.com/api/v3",
        "github_clone_url": "git@github.COMPANYNAME.com"
        }

for github.

You may ignore or omit the oath and webhook secret entries as auth isn't currently
enabled. The access/private tokens must have enough credentials for the looper to 
use the API to list repos and branches.

For gitlab, 'group' defines the prefix for all repos that test-looper will show.
For github, 'owner' defines the owner of the repos to show (an organization or person).
The urls may be modified to point at enterprise editions of the services or the hosted
versions.

Webhooks should be installed in an project you want the looper to watch automatically.
The hook should be configured to send push events to 
    
    https://testlooper.COMPANYNAME.com[:PORT]/githubReceivedAPush

## Cloud configuration

The default configuration of TestLooper looks like this:

    "machine_management": {
        "worker_name": "test-looper-worker-dev",
        "region": "us-east-1",
        "vpc_id": "vcp-XXXX",
        "security_group": "sg-XXXX",
        "subnet":"subnet-XXXX",
        "keypair": "key-pair-name",
        "bootstrap_bucket": "testlooper-COMPANYNAME",
        "bootstrap_key_prefix": "testlooper_bootstraps",
        "worker_iam_role_name": "TestLooperIamRole",
        "path_to_keys": "$HOME/.ssh/id_rsa",
        "instance_types": [
            [{"cores": 2, "ram_gb": 4}, "t2.medium"],
            [{"cores": 4, "ram_gb": 16}, "m5.xlarge"],
            [{"cores": 32, "ram_gb": 244}, "r3.8xlarge"]
            ],
        "linux_ami": "ami-55ef662f",
        "windows_ami": "ami-08910872",
        "host_ips": {
            "gitlab.COMPANYNAME.com": "...",
            "testlooper.COMPANYNAME.com": "..."
            },
        "max_workers": 8
        "max_cores": 100
        "max_ram_gb": 1000
        }

which controls how TestLooper handles booting machines in AWS. You must
expose AWS credentials for TestLooper to use in the normal ways boto3 expects them:
either as environment variables, or as config in the expected places.

Briefly the options are:

* worker_name: defines a tag that we put on all of our worker machines. We'll
only shut down machines with this tag. This lets us run multiple looper instances
in the same AWS account without conflicting.
* region: the aws region to boot machines into. make sure this is the same
as your artifact storage or you'll get charged for data transfer.
* vpc_id: the VPC into which to boot workers. required.
* security_group: the security group for workers. required.
* subnet: the subnet for workers. required.
* keypair: the keypair to boot workers with so you can login.
* bootstrap_bucket: the name of an S3 bucket where TestLooper can put
commands for workers to execute. This must be accessible by the looper server,
so if it's not a public bucket (and it shouln't be) you'll need to make sure
that the boto3 credentials can write to this bucket.
* bootstrap_key_prefix: prefix of keys written to bootstrap_bucket
* worker_iam_role_name: an IAM role that the workers will be booted with.
This needs to be able to write to the artifacts S3 bucket and communicate
with the server.
* path_to_keys: a path to a local ssh key that has rights to pull code
from the source control server. These keys get shipped to the workers
through the user-data field so make sure you're OK with that.
* instance_types: instances we're willing to boot
* linux_ami: the name of the base AMI to use for linux workers. you shouldn't
need to change this.
* windows_ami: the name of the base AMI to use for windows workers. Usually
this will be specified in individual tests.
* host_ips: a dictionary of hostnames and ips to expose. This is useful when
the workers need to connect back to services running in a corporate network
or on a non-public dns.
* max_workers: max number of instances we'll boot at any one time
* max_cores: max number of cores we'll boot at any one time
* max_ram_gb: max amount of memory (in gb) we'll boot at any one time.

TestLooper workers are configured with a bootstrap script that connects
back to the test-looper server on the host and port specified by the
server config.

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
