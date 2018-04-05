# TestLooper server setup and administration

## Server Dependencies

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
TestLooper source. 

## Starting/Stopping the system

From the 'deploy' directory, modify 'config.json' to your taste and then run

    ./redis_ctl.sh start
    ./looper_ctl.sh start

which will boot daemons for both redis and TestLooper-server.

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

For gitlab, 'group' defines the prefix for all repos that TestLooper will show.
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
back to the TestLooper server on the host and port specified by the
server config.
