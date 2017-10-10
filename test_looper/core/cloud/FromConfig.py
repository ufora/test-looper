import test_looper.core.cloud.Ec2Connection as Ec2Connection
import test_looper.core.cloud.NoCloud as NoCloud

def fromConfig(config):
    if 'cloud' not in config:
        return NoCloud.NoCloud()

    if config['cloud']['type'] == 'AWS':
        return Ec2Connection.Ec2Connection.fromConfig(config)
    else:
        raise Exception("The only cloud we know about right now is AWS")
