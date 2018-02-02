########################################
# TEST LOOPER DAEMON CONFIGURATION

#you may override this to be the root of the test-looper source tree.
PROJ_ROOT=`cd ..; pwd`

#set this to a directory where logfiles, pidfiles, etc
#can live.
TEST_LOOPER_INSTALL=`pwd`

#set this to the host you want the http->https redirect to go to
REDIRECT_HOSTNAME=$HOSTNAME

#Set this to the redis port you want to use if you don't configure
#this to use regular redis. Note that you must make sure this
#corresponds with the port listed in config.json
REDIS_PORT=1115
