########################################################################
#this is a bootstrap script to initialize a single-shot Windows test looper worker
#we install python and git
########################################################################

$machineId = Invoke-RestMethod -uri http://169.254.169.254/latest/meta-data/instance-id

$client = New-Object System.Net.WebClient

function log($msg) {
	$client.DownloadString("https://__testlooper_server_and_port__/machineHeartbeatMessage?machineId=" + $machineId + "&heartbeatmsg=" + [uri]::EscapeUriString($msg))	
}

log("Mounting disks")

C:\ProgramData\Amazon\EC2-Windows\Launch\Scripts\InitializeDisks.ps1

try {
	Set-Partition -DriveLetter "D" -NewDriveLetter "Z"
} catch { }

log("Running bootstrap script - installing python")

#download and intall python2.7.14
$python_msi_url = "https://__testlooper_server_and_port__/python-2.7.14.amd64.msi"
$python_msi_file = "C:\ProgramData\TestLooper\python-2.7.14.amd64.msi"

$client.DownloadFile($python_msi_url, $python_msi_file)
Start-Process "msiexec.exe" -ArgumentList @("/i", $python_msi_file, 'ALLUSERS="1"', "/passive", "/quiet", "/L*V", "C:\ProgramData\TestLooper\python-install.log") -Wait
$env:Path += ";C:\Python27;C:\Python27\Scripts"

#get pip
$client.DownloadFile("https://__testlooper_server_and_port__/get-pip.py", "C:\ProgramData\TestLooper\get-pip.py")
python "C:\ProgramData\TestLooper\get-pip.py"

pip install simplejson==3.13.2 requests==2.18.4 pyyaml==3.12 boto3==1.5.8 pyodbc==4.0.21 psutil==5.4.3

log("Running bootstrap script - installing git")

#download and install git 2.15.1
$git_for_windows_url = "https://__testlooper_server_and_port__/Git-2.15.1.2-64-bit.exe"
$git_for_windows_file = "C:/ProgramData/TestLooper/git_installer.exe"
$client.DownloadFile($git_for_windows_url, $git_for_windows_file)

Start-Process $git_for_windows_file -ArgumentList @("/silent", "/suppressmsgboxes", "/norestart") -Wait
$env:Path += ";C:\Program Files\Git\bin"

log("Running bootstrap script - getting the looper source")

#get the test-looper source
$testlooper_src_url = "https://__testlooper_server_and_port__/test_looper.zip"
$testlooper_zip_file = "C:\ProgramData\TestLooper\test_looper.zip"


$env:PYTHONPATH = "C:\ProgramData\TestLooper"

echo '__test_config__' | Out-File -FilePath C:\ProgramData\TestLooper\config.json -Encoding ASCII

log("Executing the test-looper worker process.")

#run the test-looper worker
$reboot_count = 0

while ($true) {
	$curtime = Get-Date -Format g
	$reboot_count += 1
	echo "Starting test-looper iteration $reboot_count at $curtime" | Out-File -Append -File C:\ProgramData\TestLooper\worker_log.txt -Encoding ASCII

	python C:\ProgramData\TestLooper\test_looper\worker\test-looper.py `
		C:\ProgramData\TestLooper\config.json `
		$machineId `
		Z:\test_looper `
		| Out-File -Append -File C:\ProgramData\TestLooper\worker_log.txt -Encoding ASCII

	echo "Test-looper exited. Re-downloading source" | Out-File -Append -File C:\ProgramData\TestLooper\worker_log.txt -Encoding ASCII
	Remove-Item $testlooper_zip_file -Force
	Remove-Item C:\ProgramData\TestLooper\test_looper -Force -Recurse
	
	$succeeded = 0
	while ($succeeded -eq 0) {
		try {
			$client.DownloadFile($testlooper_src_url, $testlooper_zip_file)
			$succeeded = 1
		} catch {
			echo "Test-looper server not available. Sleeping..." | Out-File -Append -File C:\ProgramData\TestLooper\worker_log.txt -Encoding ASCII
			sleep 2
		}
	}

	Expand-Archive -Path $testlooper_zip_file C:\ProgramData\TestLooper
}
