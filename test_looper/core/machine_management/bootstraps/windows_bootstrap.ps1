########################################################################
#this is a bootstrap script to initialize a single-shot Windows test looper worker
#we install python and git
########################################################################

$client = New-Object System.Net.WebClient

#download and intall python2.7.14
$python_msi_url = "http://www.python.org/ftp/python/2.7.14/python-2.7.14.amd64.msi"
$python_msi_file = "D:\TestLooper\python-2.7.14.amd64.msi"

$client.DownloadFile($python_msi_url, $python_msi_file)
Start-Process "msiexec.exe" -ArgumentList @("/i", $python_msi_file, 'ALLUSERS="1"', "/passive", "/quiet", "/L*V", "D:\TestLooper\python-install.log") -Wait
$env:Path += ";C:\Python27;C:\Python27\Scripts"

#get pip
$client.DownloadFile("https://__testlooper_server_and_port__/get-pip.py", "D:\TestLooper\get-pip.py")
python "D:\TestLooper\get-pip.py"

pip install simplejson==3.13.2 requests==2.18.4 pyyaml==3.12 boto3==1.5.8

#download and install git 2.15.1
$git_for_windows_url = "https://github.com/git-for-windows/git/releases/download/v2.15.1.windows.2/Git-2.15.1.2-64-bit.exe"
$git_for_windows_file = "C:/ProgramData/TestLooper/git_installer.exe"
$client.DownloadFile($git_for_windows_url, $git_for_windows_file)

Start-Process $git_for_windows_file -ArgumentList @("/silent", "/suppressmsgboxes", "/norestart") -Wait
$env:Path += ";C:\Program Files\Git\bin"

#download an extract openssh, which we need for to convert the powershell terminal output into xterminal
#friendly output. All we use from this is the ssh-shellhost.exe.
$open_ssh_url = "https://github.com/PowerShell/Win32-OpenSSH/releases/download/0.0.24.0/OpenSSH-Win64.zip"
$open_ssh_zipfile = "D:\TestLooper\OpenSSH-Win64.zip"
$client.DownloadFile($open_ssh_url, $open_ssh_zipfile)
Expand-Archive -Path $open_ssh_zipfile -DestinationPath "C:\Program Files"

#get the test-looper source
$testlooper_src_url = "https://__testlooper_server_and_port__/test_looper.zip"
$testlooper_zip_file = "D:\TestLooper\test_looper.zip"

$client.DownloadFile($testlooper_src_url, $testlooper_zip_file)
Expand-Archive -Path $testlooper_zip_file D:\TestLooper

$env:PYTHONPATH = "D:\TestLooper"

echo '__test_config__' | Out-File -FilePath D:\TestLooper\config.json -Encoding ASCII

$machineId = Invoke-RestMethod -uri http://169.254.169.254/latest/meta-data/instance-id

#run the test-looper worker
python D:\TestLooper\test_looper\worker\test-looper.py `
	D:\TestLooper\config.json `
	$machineId `
	D:\TestLooper\Storage `
	| Out-File -File D:\TestLooper\worker_log.txt -Encoding ASCII
