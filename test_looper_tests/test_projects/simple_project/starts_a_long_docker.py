#!/usr/bin/python3

import docker
import os
import sys
import time

passwd = "t3stPa__w0rd"

container = docker.from_env().containers.run(
	'microsoft/mssql-server-linux:2017-GA', 
	detach=True,
	environment={
		"ACCEPT_EULA": "Y",
		"MSSQL_SA_PASSWORD": passwd,
		"MSSQL_PID":"Developer",
		},
	name="test_sql_server"
	)

def query(query):
	return container.exec_run(
		["/opt/mssql-tools/bin/sqlcmd", 
			"-S", "localhost", 
			"-U", "SA", 
			"-P", passwd, 
			"-Q", query
			]
		)

t0 = time.time()
while time.time() - t0 < 30:
	res = query("SELECT 1")
	print("query result = ", repr(res))
	if b'1 rows affected' in res:
		print("OK")
		sys.exit(0)
	else:
		time.sleep(1)

print("FAIL")
sys.exit(1)

