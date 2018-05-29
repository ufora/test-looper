#!/bin/bash

kill_child_processes() {
  PGID=$(ps -o pgid= $$ | grep -o [0-9]*)

  pkill --group $PGID
}

trap "kill_child_processes" EXIT

PROJ_ROOT=`cd ../..; pwd`

export PYTHONPATH=$PROJ_ROOT

export TEST_LOOPER_INSTALL=$PROJ_ROOT/test_looper_tests/system_test/test_looper_install

function rebuild {

rm -rf $TEST_LOOPER_INSTALL
mkdir $TEST_LOOPER_INSTALL

mkdir -p $TEST_LOOPER_INSTALL/redis
mkdir -p $TEST_LOOPER_INSTALL/repos/simple_project
mkdir -p $TEST_LOOPER_INSTALL/repos/simple_project_2
mkdir -p $TEST_LOOPER_INSTALL/repos/simple_project_3
mkdir $TEST_LOOPER_INSTALL/logs

export GIT_AUTHOR_DATE="1509599720 -0500"
export GIT_COMMITTER_DATE="1509599720 -0500"

echo "building repos at "$TEST_LOOPER_INSTALL/repos

cd $TEST_LOOPER_INSTALL/repos/simple_project
git init .
cp $PROJ_ROOT/test_looper_tests/test_projects/simple_project/* -r .
git add .
GIT_COMMITTER_DATE="1512679665 -0500" git commit -m "a message" --date "1512679665 -0500" --author "test_looper <test_looper@test_looper.com>"

PROJ_1_COMMIT=`git rev-parse HEAD`
echo "PROJ_1_COMMIT is $PROJ_1_COMMIT"

echo "this is a file" > a_file.txt
git add .
git commit -m "second commit"
git checkout HEAD^

echo "this is a file 2" > a_file_2.txt
git add .
git commit -m "third commit"

echo "this is a file 3" > a_file_3.txt
git add .
git commit -m "fourth commit"

git merge HEAD@{3} -m 'this is a merge'
git checkout -B master2 HEAD
git checkout -B master HEAD

PROJ_1_COMMIT_POSTMERGE=`git rev-parse HEAD`

for m in 4 5 6 7 8;
do
echo "this is a file $m" > a_file_$m.txt
git add .
git commit -m "commit $m"
done

rm build_file
git add .

LONG_COMMIT_MESSAGE=$(cat <<-END
    Commit that breaks the build.

    This is a very long commit message. It's intended to help us see in the UI how we will render commit messages that are extremely long and don't have newlines. Some codebases have this in abundance.

    blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah blah
END
)

git commit -m "$LONG_COMMIT_MESSAGE"

git checkout HEAD --detach

cd $TEST_LOOPER_INSTALL/repos/simple_project_2
git init .
cp $PROJ_ROOT/test_looper_tests/test_projects/simple_project_2/* -r .
sed -i -e "s/__replace_this_hash__/$PROJ_1_COMMIT/g" testDefinitions.yml
echo "FIRST SED OK"
git add .
git commit -m "initial commit in simple_project_2"
echo "this is a file in simple_project_2" > a_file_in_repo_2.txt
git add .
git commit -m "second commit in simple_project_2"

echo "s/$PROJ_1_COMMIT/notavalidhash/g"
sed -i -e "s/$PROJ_1_COMMIT/notavalidhash/g" testDefinitions.yml
git add .
git commit -m "commit that produces a bad dependency"

rm testDefinitions.yml
git add .
git commit -m "commit that has no test file"

git checkout HEAD^^ -- .

sed -i -e "s/$PROJ_1_COMMIT/$PROJ_1_COMMIT_POSTMERGE/g" testDefinitions.yml
git commit -a -m "replacing the state to a good one"

PROJ_2_COMMIT_POSTMERGE=`git rev-parse HEAD`

git checkout HEAD --detach

cd $TEST_LOOPER_INSTALL/repos/simple_project_3
git init .
cp $PROJ_ROOT/test_looper_tests/test_projects/simple_project_3/* -r .
sed -i -e "s/__replace_this_hash__/$PROJ_2_COMMIT_POSTMERGE/g" testDefinitions.yml
echo "0" > file.txt
git add .
git commit -m "initial commit in simple_project_3"
echo "1" > file.txt
git add .
git commit -m "second commit in simple_project_3"
echo "2" > file.txt
git add .
git commit -m "third commit in simple_project_3"

git checkout HEAD --detach

}

if [ "$1" != "--norebuild" ]; then
	rebuild;

	(
		sleep 4
		echo "TOGGLING BRANCH ENABLE"
		curl "http://localhost:9081/toggleBranchUnderTest?repo=simple_project&redirect=%2Fbranches%3FrepoName%3Dsimple_project&branchname=master"
	)&

fi

REDIS_PORT=1115

(
	/usr/bin/redis-server --port $REDIS_PORT \
		--logfile $TEST_LOOPER_INSTALL/redis/log.txt --dbfilename db.rdb \
		--dir $TEST_LOOPER_INSTALL/redis --save 900 1 --save 300 10 --save 60 10000
)&


echo "BOOTING SERVER"
python -u $PROJ_ROOT/test_looper/server/test-looper-server.py $PROJ_ROOT/test_looper_tests/system_test/config.json

