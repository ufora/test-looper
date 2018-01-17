import test_looper.core.algebraic as algebraic

HardwareConfig = algebraic.Alternative("HardwareConfig")
HardwareConfig.Config = {
    "cores": int,
    "ram_gb": int
    }

MachineManagementConfig = algebraic.Alternative("MachineManagementConfig")

#boot workers in AWS
MachineManagementConfig.Aws = {
    "region": str,               #region to boot into
    "vpc_id": str,               #id of vpc to boot into
    "subnet": str,               #id of subnet to boot into
    "security_group": str,       #id of security group to boot into
    "keypair": str,              #security keypair name to use
    "bootstrap_bucket": str,     #bucket to put windows bootstrap scripts into.
    "bootstrap_key_prefix": str, #key prefix for windows bootstrap scripts.
    "worker_name": str,          #name of workers. This should be unique to this instll.
    "worker_iam_role_name": str, #AIM role to boot workers into
    "linux_ami": str,            #default linux AMI to use when booting linux workers
    "windows_ami": str,          #default AMI to use when booting windows workers. Can be overridden for one-shot workers.
    "path_to_keys": str,         #path to ssh keys to place on workers to access source control.
    "instance_types": algebraic.Dict(HardwareConfig, str),
                                 #dict from hardware configuration to instance types we're willing to boot
    "host_ips": algebraic.Dict(str, str),
                                 #dict from hostname to ip address to make available to workers
                                 #this is primarily useful when workers don't have access to dns
                                 #but we still want certs to all be valid
    "max_cores": int,            #cap on the number of cores we're willing to boot. -1 means no limit
    "max_ram_gb": int,           #cap on the number of gb of ram we're willing to boot. -1 means no limit
    "max_workers": int,          #cap on the number of workers we're willing to boot. -1 means no limit
    }

#run workers in-proc in the server
MachineManagementConfig.Local = {
    "local_storage_path": str,   #local disk storage we can use for workers
    "docker_scope": str,         #local scope to augment dockers with
    "max_cores": int,            #cap on the number of cores we're willing to boot. -1 means no limit
    "max_ram_gb": int,           #cap on the number of gb of ram we're willing to boot. -1 means no limit
    "max_workers": int,          #cap on the number of workers we're willing to boot. -1 means no limit
    }

#run workers in-proc in the server
MachineManagementConfig.Dummy = {
    "max_cores": int,            #cap on the number of cores we're willing to boot. -1 means no limit
    "max_ram_gb": int,           #cap on the number of gb of ram we're willing to boot. -1 means no limit
    "max_workers": int,          #cap on the number of workers we're willing to boot. -1 means no limit
    }

#server port config
CertsPath = algebraic.Alternative("CertsPath")
CertsPath.Paths = {
    "cert": str,
    "key": str,
    "chain": str
    }

DatabaseConfig = algebraic.Alternative("DatabaseConfig")
DatabaseConfig.InMemory = {}
DatabaseConfig.Redis = {"port": int, "db": int}

ServerConfig = algebraic.Alternative("ServerConfig")
ServerConfig.Config = {
    "path_to_certs": algebraic.Nullable(CertsPath),
    "path_to_local_repos": str,
    "database": DatabaseConfig,
    "path_to_keys": str         #path to ssh key to use to access repos
    }

ServerPortConfig = algebraic.Alternative("ServerPortConfig")
ServerPortConfig.Config = {
    "server_address": str,
    "server_https_port": int,
    "server_worker_port": int,
    "server_worker_port_use_ssl": bool,
    }

ArtifactsConfig = algebraic.Alternative("ArtifactsConfig")

ArtifactsConfig.LocalDisk = {
    "path_to_build_artifacts": str,
    "path_to_test_artifacts": str
    }

ArtifactsConfig.S3 = {
    "bucket": str,
    "region": str,
    "build_artifact_key_prefix": str,
    "test_artifact_key_prefix": str
    }

SourceControlConfig = algebraic.Alternative("SourceControlConfig")
SourceControlConfig.Local = {
    'path_to_repos': str
    }

SourceControlConfig.Github = {
    'oauth_key': str,
    'oauth_secret': str,
    'webhook_secret': str,
    'owner': str,               #owner we use to specify which projects to look at
    'access_token': str,
    'auth_disabled': bool,
    'github_url': str,          #usually https://github.com
    'github_login_url': str,    #usually https://github.com
    'github_api_url': str,      #usually https://github.com/api/v3
    'github_clone_url': str     #usually git@github.com
    }

SourceControlConfig.Gitlab = {
    'oauth_key': str,
    'oauth_secret': str,
    'webhook_secret': str,
    'group': str,               #group we use to specify which projects to show
    'private_token': str,
    'auth_disabled': bool,
    'gitlab_url': str,          #usually https://gitlab.mycompany.com
    'gitlab_login_url': str,    #usually https://gitlab.mycompany.com
    'gitlab_api_url': str,      #usually https://gitlab.mycompany.com/api/v3
    'gitlab_clone_url': str     #usually git@gitlab.mycompany.com
    }


Config = algebraic.Alternative("Config")
Config.Config = {
    "server": ServerConfig,
    "server_ports": ServerPortConfig,
    "source_control": SourceControlConfig,
    "artifacts": ArtifactsConfig,
    "machine_management": MachineManagementConfig
    }

WorkerConfig = algebraic.Alternative("WorkerConfig")
WorkerConfig.Config = {
    "server_ports": ServerPortConfig,
    "source_control": SourceControlConfig,
    "artifacts": ArtifactsConfig,
    "cores": int,
    "ram_gb": int
    }
