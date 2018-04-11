########################################################################
#this is a bootstrap script to initialize a single-shot Windows test looper worker
#we install python and git
########################################################################

$machineId = Invoke-RestMethod -uri http://169.254.169.254/latest/meta-data/instance-id

$client = New-Object System.Net.WebClient

function log($msg) {
	$client.DownloadString("https://__testlooper_server_and_port__/machineHeartbeatMessage?machineId=" + $machineId + "&heartbeatmsg=" + [uri]::EscapeDataString($msg))	
}

log("Mounting disks")

C:\ProgramData\Amazon\EC2-Windows\Launch\Scripts\InitializeDisks.ps1

try {
	Set-Partition -DriveLetter "D" -NewDriveLetter "Z"
} catch { }

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
