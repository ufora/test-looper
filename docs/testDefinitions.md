# TestLooper test definitions

## testDefinitions.yml files

Each commit in a repo defines a set of repos, environments, tests, builds 
in a single yaml file. If this file is not present, doesn't contain valid yaml,
or fails to produce a valid set of test definitions, the commit is considered
'untestable', and while its contents may be used in other tests, that commit
doesn't itself define any given tests.

The "test-definitions" file in any given repo is determined by looking at all files,
selecting those whose name is one of "testDefinitions.yml", "testlooper.yml", or
whose name ends with ".testlooper.yml" and by taking the first such file (sorted alphabetically).

That file is parsed for tests independently on each commit that TestLooper
is aware of.

## yaml preprocessing directives

Each yaml file that TestLooper includes is first parsed by the 'yaml' python
library into core python datastructures. We then apply a few basic transformations
to the yaml file to reduce the verbosity of your file.

Note that these directives are like C++ preprocessor macros: they don't define
anything in the target language (in this case, testdefinitions) - they simply
provide a mechanism for repeating data structures and applying variable substitution.

Directives are applied in a walk through the object tree of your yaml file
(e.g. from outside to inside, not from top to bottom). At every point in the
walk, we have a set of 'active variables', where a variable and its definition
are both strings. 

All yaml strings are processed using the variables placed in scope above them.
For every variable 'v' defined, we search for '${v}' and replace it with the
variable's value.  The results are undefined if your variable names or values
happens to contain things like '$' or '{}', and at this level, we don't
support nested variable expansion (e.g. "${v_${v2}}" isn't guaranteed to work,
so please don't do that.

The following expansion directives are available:

*Define*: Any dictionary containing exactly two keys, 'define' and 'in', is
considered a 'variable definition' clause. The 'define' key must resolve to a
dictionary of strings containing 'variable definitions', which are evaluated
and then merged into the current active variables. We then continue to walk
down 'in', applying variable substitution as we go.

for example

	define:
		var1: def1:
		var2: def2:
	in:
	  	- "This is a string with no variables"
	  	- "in this string, however var1 is ${var1}"
	  	- "and in the following dictionary, "
	  	- { "${var1}": "${var2}" }
	  	- "both key and value will have had a variable substitution."

resolves to 

	- "This is a string with no variables"
	- "in this string, however var1 is def1"
	- "and in the following dictionary, "
	- { "def1": "def2" }
	- "both key and value will have had a variable substitution."

*Squash*: Any dictionary containing the exact keys 'squash' and 'over' is
considered a 'squash'. The result is always a list of dictionaries. 'over'
gets evaluated, and then flattened to a list of dictionaries.  the 'squash'
argument is then merged into each of the dictionaries. This is useful for
definining variable substitutions that have common elements.  For instance

	- squash: { common_var1: common_val1 }
	  over:
	  	- { var2: val2 }
	  	- { var2: val3 }
	  	- { var2: val4 }

resolves to 

	- { common_var1: common_val1, var2: val2 }
	- { common_var1: common_val1, var2: val3 }
	- { common_var1: common_val1, var2: val4 }

where 'common_var1' has been merged into all the children.

*Foreach*: Any dictionary containing exactly two keys, 'foreach', and 'repeat', 
defines a 'foreach' loop construct. The pattern repeats 'repeat' with variables
defined from each of the dictionaries within the 'foreach' pattern. The foreach pattern
must be a list of dictionaries of variables, (or a list of lists, which will be flattened).

If 'repeat' is a dictionary, the resulting dictionaries are merged, and items must be unique.
If 'repeat' is a list, then we concatenate the lists. 

Variable substitution occurs first on the 'foreach' items, and then the repeat is 
repeatedly evauluated with the additional replacements. For instance

	foreach:
	  - { project: p1, compiler: gcc4.8 }
	  - { project: p2, compiler: gcc4.8 }
	  - { project: p2, compiler: gcc5.2 }
	repeat:
	  ${project}/build/${compiler}:
	    environment: env-${compiler}

expands to 

	  p1/build/gcc4.8:
	    environment: env-4.8
	  p2/build/gcc4.8:
	    environment: env-4.8
	  p2/build/gcc5.2:
	    environment: env-5.2

## Test definitions files

Each test definitions file consists of six basic entries. Only 'looper_version' is 
strictly required.

* `looper_version` (currently 3), identifying which version we're running against.
* `repos` defining a set of repos we are referring to.
* `includes` defining a set of external file includes.
* `environments` defining a set of environments.
* `builds` defining a set of build steps
* `tests` defining a set of test steps
* `deployments` defining a set of deployments

### Repo definitions

Each testDefinitions file defines a set of named repos. Repos are used to refer
to a specific commit in the same or another repository. Each repo
gets a name in the local namespace, and consists of a 'reference', which defines
unambiguously what commit the repo reference targets, as well as an optional 
tracking branch.

All testdefinitions files have an implied repo called 'HEAD' which can be used
to refer to the source tree of the current commit that contains the test definition
files.

A repo definition (the right-hand side of the 'repos' field) must be a dictionary
from string (containing reponames which must be 'identifiers' in the python sense)
to a repo def. A repo def may be one of:
	
* a single string containing a reference
* a dictionary containing 'reference', 'branch', and (optionally 'auto').
* an 'import' containing a reference to a repo within another already defined repo

A repo reference consists of the name of the repository on the git server, followed
by a commit hash. Note that this must be a full hash, not a branchname, tag, or anything
whose meaning could change.

As an example:

	...
	repos:
		testlooper_repo: ufora/testlooper/5e1d0fa898db6236369a9b13f7b5da590922c77d
		testlooper_master: 
			reference: ufora/testlooper/5e1d0fa898db6236369a9b13f7b5da590922c77d
			branch: master
			auto: true
		testlooper_import: 
			import: testlooper_repo/some_repo_name

defines three repos. The first, `testlooper_repo` is considered a 'fixed pin',
meaning that it refers to a source tree in the 'ufora/testlooper' repo named
by a specific sha hash. The second, `testlooper_master` refers to the same
commit hash, but is called a  'Floating' pin, and has additional tracking
information. If 'auto' is set to true, then test looper will push new commits
to this branch updating the sha-hash to whatever new commit the 'master' branch points to
If 'auto' is set to false, then  this action can be triggered by the UI.
Testlooper is careful to push pins forward in a self-consistent way, and won't
push forward if there is a cycle in the pin graph.

### Include definitions

The right-hand side of the 'includes' is a list of include directives. Each
include directive is either a string (giving the path to a yaml file), or a
dictionary `{path: str, variables: {str:str}}`.

Each path begin either with a '.', which refers to a text file relative to the
file currently being processed, or a reponame, which includes files from the
root of a named repo. Regardless of platform, we use '/' as the path
delimiter.

Variables are expanded within the file at the outermost scope. The file
referred to  by is parsed as yaml, has any variables defined by 'variables'
applied to it, and is then considered as an entire test definitions suite
(including builds, repo definitions, environments, etc) each of which is
pulled into the existing namespace.

Included repos may not be marked 'auto' since we can't update their source
directly.

### Environment definitions

Each testDefinitions defines a set of named environments. Each environment
is either a 'root environment' or a subclass/mixin environment.

Each root environment defines 

* platform - one of `linux` or `windows`. A string.
* image - either a dockerfile, or an AMI. Currently, linux implies dockerfile
	and AMI implies windows. You may supply one of the following
	* {'dockerfile_contents': str} - contents of dockerfile inline
	* {'dockerfile': str} - contents of dockerfile in a path relative to the root of the current checkout
	* {'base_ami': str, 'setup_script_contents': str} - defines the AMI (amazon machine image) to use as a starting point,
		along with a powershell script to set up the machine

Each subclass/mixin environment defines:

* base - a string or list of strings giving the base named base environments
  that this environment descends from
* setup_script_contents - a string of shell script that gets merged into 
  the dockerfile or setup_script of the final environment

All environments define the following

* variables - a key-value dictionary of strings containing environment variables
  that will be in scope during test execution
* dependencies - a key-value dictionary of strings describing the 
  external dependencies that get exposed to 
  the process wile it's running. These may be dependencies on source trees
  or the results of other builds. The key determines the location on disk
  where the binary input will reside.

The following variables really refer to tests, but can be supplied as defaults
through the environment:

* test_configuration - a value to place in the configuration of any test or
  build that uses this environment if not overridden.
* test_preCommand - shell script text to prepend to the test or build command
* test_preCleanupCommand - shell script text to prepend to the test or build cleanup command
* test_timeout - maximum number of seconds the test can run for before being considered 'timed out'
* test_min_cores - minimum number of cores that must be present to run this test
* test_max_cores - the maximum number of cores that this test or build can profitably use. Set higher
  if you'd like the test or build to run on a bigger box.
* test_min_ram_gb - the minimum amount of ram this test or build needs to be run
* test_max_retries - the maximum number of times we'll retry this before giving up.
* test_retry_wait_seconds - the number of seconds to wait between retries.

#### Inheritance

Environments may inherit from each other and use multiple inheritance. This
allows us to define 'mixin' behaviors which can simplify complex setups. For
instance, a mixin environment can override some environment variables, or add
additional setup commands to be run before testing.

Environments with base classes may have conflicting variable definitions
or dependency definitions. To resolve these, we follow python method resolution order:
a child overrides its base class, and the first mixin dominates the second. Setup scripts,
test_cleanupCommand, and test_preCleanupCommand follow a concatenation policy
(rather than replacement), so that a base class can define some setup, and a
child can extend it. Each named environment is
considered only once in the textual merge. 

#### Dependencies

Dependencies are specified as key-value pairs. The key determines where the dependency
will be exposed on disk, and the value specifies what the dependency is. A key of 'KEY'
will be located at '${TEST_INPUTS}/KEY'. Key may have '/' in it, which will be mapped
to subdirectories.

Dependency values may be 

* `HEAD`, indicating to map the current source tree containing the test definition file
* `reponame`, indicating to map source code of the given named repo to this location
* `build_name`, indicating to map the binary output of the build step named 
  'build_name' from the current namespace
* `reponame/source/path`, indicating to map the directory of the repo 'reponame' indicated
  by 'path' to this location. TestLooper is careful to check the last time content
  in this directory was modified and use then when hashing the build.
* `reponame/build_name`, indicating to map the build output of the build specified
  by 'build_name' within the tests defined by the remote repo.

### Test, Build, and Deployment definitions

The category of test, build, or deployment is determined by which root section the
object resides in (`tests`, `builds`, or `deployments`). 
Each such category must be a dictionary from a string (name) to a definition, or may
be a list of dictionaries, which get flattened during preprocessing. The name of a
build defines how it is referred to in the dependencies of other tests and builds,
and must be unique across the commit's namespace.

Each category has a number of items in common:

* `command` - a string indicating the command (bash on linux, powershell on windows) to execute
* `environment` - a string giving the named environment we want to run in. If not present,
  then we take the name of the test, split it on '/', and take the last portion
* `mixins` - a list of environments to mixin to this test definition.
* `configuration` - a string (used only for display purposes) defining what 'configuration' this
  test or build belongs to. Defaults to the environment name.
* `project` - a string (used only for display purposes) defining the project. If not 
  present, we take the name of the test, split it on '/', and take the _first_ portion.
* `dependencies` - dependency dict with the same semantics as an environment
* `variables` - a variable dict with the same semantics as an environment
* `timeout` - maximum number of seconds the test can run for before being considered 'timed out'
* `min_cores` - minimum number of cores that must be present to run this test
* `max_cores` - the maximum number of cores that this test or build can profitably use. Set higher
  if you'd like the test or build to run on a bigger box.
* `min_ram_gb` - the minimum amount of ram this test or build needs to be run

Builds may also define 

* `cleanup` - a string giving a command  which runs after the test or build, regardless of success
  or failure, as a way of marshalling artifacts.
* `max_retries` - maximum number of times to retry the build. If not given, or zero, we don't retry.
* `retry_wait_seconds` - minimum number of seconds to wait before retrying a build if retry is on.

Builds and tests both proceed in a similar fashion: first their dependencies are marshalled to
the `${TEST_INPUTS}` directory. Then the `command` is run. If the exit code is zero, then the
test or build has succeeded. 

For builds, the 'build output' is gathered by pulling the contents of `${TEST_BUILD_OUTPUT_DIR}`.
This gets zipped or tarballed (depending on platform) and gets inflated in the appropriate
place for downstream steps.

For tests, we walk all files and directories in `${TEST_OUTPUT_DIR}` and upload their
contents as test artifacts. Directories get tarballed before upload.

Prior to tests running, all the variable definitions given by the environment
and test are merged (with test taking precedence), and then variables get 'resolved'. 
This means we repeatedly loop over all variables, and for each variable `var`, search for strings
of the form `${var}` in other variable definitions and shell commands, which we replace with
the variable's value. We support chains of variable definition and nested variables, and we're
careful not to expand cycles if you make a mistake and create one.

At runtime we also define a few specific variables:

* `TEST_CORES_AVAILABLE` - number of cores we're allowed to use
* `TEST_RAM_GB_AVAILABLE` - number of GB of ram we may use
* `HOSTNAME` - set to 'testlooperworker'
* `PYTHONUNBUFFERED` - set to TRUE to make sure we get output from test programs
* `PERL_BIN` - (windows only) path to the perl.exe contained in git-for-windows
* `GIT_BIN` - (windows only) path to git.exe
* `TEST_INPUTS` - path to directory where test inputs are mounted
* `TEST_SCRATCH_DIR` - path to directory we can use for scratch space
* `TEST_OUTPUT_DIR` - path to directory where we should place test artifacts
* `TEST_BUILD_OUTPUT_DIR` - path to output of build steps
* `TEST_CCACHE_DIR` - location to use for ccache if we're using it
* `TEST_LOOPER_TEST_ID` - a unique id for the current test or build run

Test runs may optionally produce a 'testSummary.json' file. This allows you to 
specify the success or failure of individual tests within the run. The format of this file should be

	{
		"testname_1": {
			"success": (true|false)
			"logs": [ "path/to/logfile1", "path/to/logfile2" ]
		},
		...
	}

Testlooper will report individual test success and link to the individual logs of each test in
test display. These paths should be absolute paths!

Deployments just define test entrypoints that can be booted on demand in the UI, but which don't run
as part of a regular test run.
