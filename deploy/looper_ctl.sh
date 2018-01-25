#!/bin/bash

set -e

source config.sh

export PYTHONPATH=$PROJ_ROOT
export TEST_LOOPER_INSTALL=$TEST_LOOPER_INSTALL
export REDIS_PORT=$REDIS_PORT

mkdir -p $TEST_LOOPER_INSTALL/pidfiles
mkdir -p $TEST_LOOPER_INSTALL/logs

# Must be a valid filename
NAME=test_looper_server
LOGFILE=$TEST_LOOPER_INSTALL/logs/$NAME.log
PIDFILE=$TEST_LOOPER_INSTALL/pidfiles/$NAME.pid
#This is the command to be run, give the full pathname

DAEMON=/usr/bin/python
DAEMON_OPTS="-u $PROJ_ROOT/test_looper/server/test-looper-server.py $TEST_LOOPER_INSTALL/config.json "

export PATH="${PATH:+$PATH:}/usr/sbin:/sbin"

case "$1" in
  start)
        echo -n "Starting daemon: "$NAME
	start-stop-daemon --start --quiet --pidfile $PIDFILE --make-pidfile --background --startas /bin/bash -- -c "echo $DAEMON_OPTS >> $LOGFILE; exec $DAEMON $DAEMON_OPTS >> $LOGFILE 2>&1 "
        echo "."
	;;
  stop)
        echo -n "Stopping daemon: "$NAME
	start-stop-daemon --stop --quiet --oknodo --pidfile $PIDFILE --remove-pidfile
        echo "."
	;;
  restart)
        echo -n "Restarting daemon: "$NAME
	start-stop-daemon --stop --quiet --oknodo --retry 30 --pidfile $PIDFILE --remove-pidfile
	start-stop-daemon --start --quiet --pidfile $PIDFILE --make-pidfile --background --startas /bin/bash -- -c "echo $DAEMON_OPTS >> $LOGFILE; exec $DAEMON $DAEMON_OPTS >> $LOGFILE 2>&1 "
	echo "."
	;;

  *)
	echo "Usage: "$1" {start|stop|restart}"
	exit 1
esac

exit 0
