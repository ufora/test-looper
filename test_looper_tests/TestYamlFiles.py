repo0 = """
looper_version: 5
environments:
  repo0_env:
    platform: linux
    image:
      dockerfile_contents: hi
    variables:
      ENV: repo0
"""

repo1 = """
looper_version: 5
repos:
  repo0c0: repo0/c0
  repo0c1: repo0/c1
environments:
  linux: 
    platform: linux
    image:
      dockerfile: "test_looper/Dockerfile.txt"
    variables:
      ENV_VAR: LINUX
  windows: 
    platform: windows
    image:
      base_ami: "ami-123"
      setup_script_contents: |
        echo 'ami-contents'
    variables:
      ENV_VAR: WINDOWS
    dependencies:
      dep0: repo0c0
  windows_2:
    base: windows
    variables:
      ENV_VAR: OVERRIDDEN
      ENV_VAR2: WINDOWS_2
    setup_script_contents: |
      echo 'more ami contents'
    dependencies:
      dep1: repo0c1
builds:
  build/linux:
    dependencies:
      src: HEAD
    command: "src/build.sh"
    min_cores: 1
    max_cores: 1
tests:
  test/linux:
    command: "test.sh"
    dependencies:
      src: HEAD
      build: build/linux
    min_cores: 4
  test/windows:
    command: "src/test.py"
    dependencies:
      src: HEAD
"""
repo2 = """
looper_version: 5
repos:
  child: repo1/c0
  repo0c0: repo0/c0
  repo0c1: repo0/c1
environments:
  linux: 
    base: child/linux
    variables:
      ENV_VAR_2: LINUX_2
    dependencies:
      dep1: repo0c1
  windows:
    base: child/windows
    variables:
      ENV_VAR_2: WINDOWS_2
    dependencies:
      dep1: repo0c1
  windows_2: 
    base: child/windows_2
    variables:
      ENV_VAR_2: WINDOWS_3
    dependencies:
      dep2: repo0c1
  test_linux:
    platform: linux
    image:
      dockerfile: "test_looper/Dockerfile.txt"
    variables:
      ENV_VAR: ENV_VAL
builds:
  - foreach: {env: [linux]}
    repeat:
      build/${env}:
        command: "src/build.sh $TEST_LOOPER_IMPORTS/child"
        dependencies:
          child: child/build/${env}
          src: HEAD
  - build_without_deps/linux:
      dependencies:
        src: HEAD
      command: "src/build.sh"
tests:
  foreach: {env: [linux]}
  repeat:
      test/${env}:
        command: "src/test.sh $TEST_LOOPER_IMPORTS/build"
        dependencies:
          src: HEAD
          build: build/${env}
"""

repo3 = """
looper_version: 5
repos:
  child: repo2/c0
environments:
  linux: 
    base: child/linux
builds:
  build/linux:
    command: "src/build.sh $TEST_LOOPER_IMPORTS/child"
    dependencies:
      src: HEAD
      child: child/build/linux
  build_without_deps/linux:
    command: "build.sh"
"""

repo4 = """
looper_version: 5
environments:
  windows_good: 
    platform: windows
    image:
      base_ami: "ami-123"
  windows_bad: 
    platform: windows
    image:
      base_ami: "not_an_ami"
builds:
  build/windows_good:
    dependencies: 
      src: HEAD
    command: "src/build.sh"
  build/windows_bad:
    dependencies: 
      src: HEAD
    command: "src/build.sh"
"""

repo5 = """
looper_version: 5
repos:
  child: 
    reference: repo2/c0
    branch: master
    auto: true
"""

repo5_nopin = """
looper_version: 5
repos:
  child: 
    reference: repo2/c0
"""

repo6 = """
looper_version: 5
repos:
  child: 
    reference: repo6/c0
    branch: __branch__
    auto: true
"""

repo6_twopins = """
looper_version: 5
repos:
  child: 
    reference: repo6/HEAD1
    branch: __branch__
    auto: true
  child2: 
    reference: repo6/HEAD2
    branch: __branch2__
    auto: true
"""

repo6_headpin = """
looper_version: 5
repos:
  child: 
    reference: repo6/HEAD
    branch: __branch__
    auto: true
"""

repo6_nopin = """
looper_version: 5
repos:
  child: 
    reference: repo6/c0
"""

repo7_circular = """
looper_version: 5
environments:
  e1: 
    base: [e2]
  e2:
    base: [e1]
builds:
  build:
    environment: e1
"""

repo8_circular_builds = """
looper_version: 5
environments:
  e1: 
    platform: linux
    image:
      dockerfile: "test_looper/Dockerfile.txt"
builds:
  build2/e1:
    command: hi
    dependencies:
     input: build1/e1
  build1/e1:
    command: hi
    dependencies:
     input: build2/e1
"""

repo9_import_child_refs = """
looper_version: 5
repos:
  repo2_ref: repo2/c0
  repo0c0_ref: 
    import: repo2_ref/child/repo0c0
  repo1c0_ref: 
    import: repo2_ref/child
environments:
  repo0_env: 
    base: repo0c0_ref/repo0_env
  repo1_env: 
    base: repo1c0_ref/linux
  repo2_env: 
    base: repo2_ref/linux
builds:
  build/repo0_env:
    command: hi
  build/repo1_env:
    command: hi
    dependencies:
     input: repo1c0_ref/build/linux
  build/repo2_env:
    command: hi
    dependencies:
     input: repo2_ref/build_without_deps/linux
"""
