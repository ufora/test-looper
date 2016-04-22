#   Copyright 2015-2016 Ufora Inc.
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from os import getenv

output_dir = getenv('OUTPUT_DIR', '')
docker_output_dir = '/volumes/output'
docker_src_dir = '/volumes/src'
ccache_dir = getenv('CCACHE_DIR', '')

perf_test_output_file = getenv("TEST_LOOPER_PERFORMANCE_TEST_RESULTS_FILE")
repo = getenv("TEST_REPO", '')
revision = getenv('REVISION', '')
aws_availability_zone = getenv('AWS_AVAILABILITY_ZONE', '')
test_id = getenv('TEST_LOOPER_TEST_ID', '')
test_name = getenv('TEST_LOOPER_TEST_NAME', '')
multibox_test_machines = getenv('TEST_LOOPER_MULTIBOX_IP_LIST', '').replace(' ', ',')
own_ip_address = getenv('TEST_LOOPER_MULTIBOX_OWN_IP', '')


