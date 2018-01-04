########################################################################
#this is a bootstrap script to initialize a single-shot Windows test looper worker
#we install python and git
########################################################################

$machineId = Invoke-RestMethod -uri http://169.254.169.254/latest/meta-data/instance-id

$client = New-Object System.Net.WebClient

function log($msg) {
	$client.DownloadString("https://__testlooper_server_and_port__/machineHeartbeatMessage?machineId=" + $machineId + "&heartbeatmsg=" + [uri]::EscapeUriString($msg))	
}

log("Running bootstrap script - installing python")

#download and intall python2.7.14
$python_msi_url = "http://www.python.org/ftp/python/2.7.14/python-2.7.14.amd64.msi"
$python_msi_file = "C:\ProgramData\TestLooper\python-2.7.14.amd64.msi"

$client.DownloadFile($python_msi_url, $python_msi_file)
Start-Process "msiexec.exe" -ArgumentList @("/i", $python_msi_file, 'ALLUSERS="1"', "/passive", "/quiet", "/L*V", "C:\ProgramData\TestLooper\python-install.log") -Wait
$env:Path += ";C:\Python27;C:\Python27\Scripts"

#get pip
$client.DownloadFile("https://__testlooper_server_and_port__/get-pip.py", "C:\ProgramData\TestLooper\get-pip.py")
python "C:\ProgramData\TestLooper\get-pip.py"

pip install simplejson==3.13.2 requests==2.18.4 pyyaml==3.12 boto3==1.5.8 pyodbc==4.0.21

log("Running bootstrap script - installing git")

#download and install git 2.15.1
$git_for_windows_url = "https://github.com/git-for-windows/git/releases/download/v2.15.1.windows.2/Git-2.15.1.2-64-bit.exe"
$git_for_windows_file = "C:/ProgramData/TestLooper/git_installer.exe"
$client.DownloadFile($git_for_windows_url, $git_for_windows_file)

Start-Process $git_for_windows_file -ArgumentList @("/silent", "/suppressmsgboxes", "/norestart") -Wait
$env:Path += ";C:\Program Files\Git\bin"

log("Running bootstrap script - installing openssh")

#download an extract openssh, which we need for to convert the powershell terminal output into xterminal
#friendly output. All we use from this is the ssh-shellhost.exe.
$open_ssh_url = "https://github.com/PowerShell/Win32-OpenSSH/releases/download/0.0.24.0/OpenSSH-Win64.zip"
$open_ssh_zipfile = "C:\ProgramData\TestLooper\OpenSSH-Win64.zip"
$client.DownloadFile($open_ssh_url, $open_ssh_zipfile)
Expand-Archive -Path $open_ssh_zipfile -DestinationPath "C:\Program Files"

log("Running bootstrap script - getting the looper source")

#get the test-looper source
$testlooper_src_url = "https://__testlooper_server_and_port__/test_looper.zip"
$testlooper_zip_file = "C:\ProgramData\TestLooper\test_looper.zip"

$client.DownloadFile($testlooper_src_url, $testlooper_zip_file)
Expand-Archive -Path $testlooper_zip_file C:\ProgramData\TestLooper

$env:PYTHONPATH = "C:\ProgramData\TestLooper"

echo '__test_config__' | Out-File -FilePath C:\ProgramData\TestLooper\config.json -Encoding ASCII

log("Running bootstrap script - downloading the SQL install media")

Read-S3Object -BucketName "testlooper-broadwaytechnology" `
	-Key "InstallationMedia/SQLServer2016SP1-FullSlipstream-x64-ENU-DEV.iso-b12af2cc5112f22a784a14f8d32b49ee56d296b3" `
	-File D:\SQLServer.iso

Mount-DiskImage -ImagePath D:\SQLServer.iso

log("Running bootstrap script - installing the SQL installation")

echo 'INSTALLING SQL SERVER'
E:\setup.exe /Q /ACTION=Install /InstanceName=MSSQLSERVER `
	/Features=SQLEngine /INSTALLSQLDATADIR="D:\SQL\MSSQLSERVER" `
	/SQLCOLLATION="SQL_Latin1_General_CP1_CS_AS" /SAPWD="t3stPa__w0rd" `
	/IACCEPTSQLSERVERLICENSETERMS /SECURITYMODE="SQL" `
	/SQLSYSADMINACCOUNTS="Administrator" /INDICATEPROGRESS `
	/UPDATEENABLED=0 `
	/TCPENABLED=1 `
	/NPENABLED=1 `
	/HIDECONSOLE

log("Executing the test-looper worker process.")

#run the test-looper worker
python C:\ProgramData\TestLooper\test_looper\worker\test-looper.py `
	C:\ProgramData\TestLooper\config.json `
	$machineId `
	C:\ProgramData\TestLooper\Storage `
	| Out-File -File C:\ProgramData\TestLooper\worker_log.txt -Encoding ASCII
