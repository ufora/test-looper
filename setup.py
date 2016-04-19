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

from setuptools import setup, find_packages
import os
import re

here = os.path.abspath(os.path.dirname(__file__))
README = open(os.path.join(here, 'README.rst')).read()
NEWS = open(os.path.join(here, 'NEWS.txt')).read()

def read_package_version():
    version_file = 'test_looper/client/_version.py'
    with open(version_file, 'rt') as version_file:
        version_line = version_file.read()
    match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]", version_line, re.M)
    if match:
        return match.group(1)
    raise RuntimeError("Can't read version string from '%s'." % (version_file,))

version = read_package_version()

install_requires = []


setup(
    name='test_looper',
    version=version,
    description="Helper package for the test-looper statistical testing service",
    long_description=README + '\n\n' + NEWS,
    classifiers=[
        # Get strings from http://pypi.python.org/pypi?%3Aaction=list_classifiers
        'Development Status :: 3 - Alpha',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 2.7',
        'Topic :: Software Development :: Testing'
    ],
    keywords='CI Continuous Integration cloud ec2 testing QA',
    author='Ufora Inc.',
    author_email='info@ufora.com',
    url='http://www.ufora.com/',
    license='Apache',
    packages=['test_looper.client', 'test_looper'],
    package_data={
        '': ['*.txt', '*.rst'],
        },
    install_requires=install_requires
)
