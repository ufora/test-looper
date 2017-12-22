########################################################################
#this is a bootstrap script to initialize a single-shot Windows test looper worker
#we install python and git
########################################################################


$client = New-Object System.Net.WebClient

#download and intall python2.7.14
$python_msi_url = "http://www.python.org/ftp/python/2.7.14/python-2.7.14.amd64.msi"
$python_msi_file = "C:\ProgramData\TestLooper\python-2.7.14.amd64.msi"

$client.DownloadFile($python_msi_url, $python_msi_file)
Start-Process "msiexec.exe" -ArgumentList @("/i", $python_msi_file, "ALLUSERS=`"1`"", "/passive", "/quiet", "/L*V", "C:\ProgramData\TestLooper\python-install.log") -Wait
$env:Path += ";C:\Python27"

#download and install git 2.15.1
$git_for_windows_url = "https://github.com/git-for-windows/git/releases/download/v2.15.1.windows.2/Git-2.15.1.2-64-bit.exe"
$git_for_windows_file = "C:/ProgramData/TestLooper/git_installer.exe"
$client.DownloadFile($git_for_windows_url, $git_for_windows_file)

Start-Process $git_for_windows_file -ArgumentList @("/silent", "/suppressmsgboxes", "/norestart") -Wait
$env:Path += ";C:\Program Files\Git\bin"

