# The TestLooper command-line front-end `tl.py`

`tl.py` lets you check out multi-repo testlooper projects, see
what your testDefinitions files define, and run build and tests commands
as defined in the testlooper definitions on those repos.

## Prerequisites

First, you need the testlooper source. Just download the repo
and stick `test_looper/script/tl.py` in your path. This works on both
linux and windows.

On linux, you'll need docker installed. On windows you need pypiwin32.
On all platforms, you need to pip install pyyaml, simplejson, requests,
and psutil.

You need a version of git installed that has support for worktrees (2.16.2 on windows,
2.5 or later on linux).

## Basic usage

To get started, run

	tl.py init <dir> git@gilab.mycompany.com

to initialize a '.tl' repo in `dir`. This operates like `git` - you have a 
`.tl` directory that identifies the root of the installation. All `tl.py` commands
operate on that installation from any subdirectory.

You may optionally pass `--repos` to provide an alternative place to stick the
checked out repos (that can be shared across multiple checkouts). You may
specify a set of prefixes to 'ignore' using `--ignore`. Repos whose name start
with any of those repos are not displayed by default, and their source trees
are placed in a 'hidden' directory. This can be used to isolate repos that
contain toolchains, etc. Finally, if you have lots of repos with a common
prefix, you may provide a list of prefixes to 'strip' from names using the
`--strip` flag. For instance if you have many repos in the 'mycompany/core'
group in gitlab, you can specify a  'mycompany/core/' prefix and get shorter
names.

cd into `dir`, and run

	tl.py checkout reponame branchname

And `tl.py` will find the repo on your git server called `reponame.git`
(searching from shorter to longer 'strip' prefix if you supplied any) and grab
the relevant commit. It will then parse the testDefinitions file and pull
any referred-to commits. Each such commit gets mounted in `<dir>/src/reponame`,
where `reponame` is the name of the repo *within the test file*. This is important
because it gives a consistent name to repos across checkouts and branches.

At this point, you may run

	tl.py info

to get a list of defined tests and builds, and 

	tl.py info <testname_or_glob> -d

to get details on exactly what will be run for that particular test.

You may then run

	tl.py run <Testname_or_glob> [-s] [-d] [-jN]

to execute one or more tests/builds. `tl.py` keeps track of builds by name
within the test definitions file, so that they're stable. If you specify
`-s`, `tl.py` will show you verbose output - otherwise it logs to a file
scoped by that particular test. If you pass `-d` it will run only the
test you specified - otherwise it will also pass over all the dependencies
as well. If you pass `-jN` it overrides the TEST_CORES_AVAILABLE environment
variable.

You may also run `tl.py status` to see what changes you've made in subrepos
and `tl.py fetch` to run a `git fetch` on all the repos in the background.

Finally, on windows, within each build directory we write a file called
'command/vars.bat' before executing the test. This contains all the
environment variables we use for that invocation so you can execute the
commands in a way that's as close as possible to how TestLooper would run
them.

