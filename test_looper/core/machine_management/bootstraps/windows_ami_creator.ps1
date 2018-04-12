<powershell>

########################################################################
#this is a bootstrap script to initialize a Windows test-looper worker.
#We install ssh keys, and then pull powershell instructions
#from a specified bucket/key pair. 
########################################################################

$machineId = Invoke-RestMethod -uri http://169.254.169.254/latest/meta-data/instance-id

$client = New-Object System.Net.WebClient

function log($msg) {
    $client.DownloadString("https://__testlooper_server_and_port__/machineHeartbeatMessage?machineId=" + $machineId + "&heartbeatmsg=" + [uri]::EscapeDataString($msg))  
}

try {

    md -Force C:\ProgramData\TestLooper
    md -Force C:\Users\Administrator\.ssh

    #fixup the hosts file.
    __hosts__

    log("Mount disks")

    C:\ProgramData\Amazon\EC2-Windows\Launch\Scripts\InitializeDisks.ps1

    try {
        Set-Partition -DriveLetter "D" -NewDriveLetter "Z"
    } catch { }

    log("Executing inital bootstrap script.")

    #export our ssh keys so 'git' can find them
    echo "__test_key__" | Out-File -FilePath "C:\Users\Administrator\.ssh\id_rsa" -Encoding ASCII
    echo "__test_key_pub__" | Out-File -FilePath "C:\Users\Administrator\.ssh\id_rsa.pub" -Encoding ASCII
    echo "StrictHostKeyChecking=no" | Out-File -FilePath "C:\Users\Administrator\.ssh\config" -Encoding ASCII

    Read-S3Object -BucketName __bootstrap_bucket__ -Key __reboot_script_key__  -File C:\ProgramData\TestLooper\RebootScript.ps1
    Read-S3Object -BucketName __bootstrap_bucket__ -Key __installation_key__  -File C:\ProgramData\TestLooper\InstallScript.ps1

    $password = "__windows_box_password__"
    $env:AdministratorPassword = $password

    $secure_password = ConvertTo-SecureString $password -AsPlainText -Force

    Set-LocalUser -Name "Administrator" -Password $secure_password
    
    Set-ItemProperty -Path “HKLM:SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon” -Name DefaultPassword -Type STR -Value $password
    Set-ItemProperty -Path “HKLM:SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon” -Name DefaultUsername -Type STR -Value "Administrator"
    Set-ItemProperty -Path “HKLM:SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon” -Name DefaultDomainName -Type STR -Value "$env:USERDOMAIN"
    Set-ItemProperty -Path “HKLM:SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon” -Name AutoAdminLogon -Type DWORD -Value 1

    #download and intall python2.7.14
    log("Installing python")
    $python_msi_url = "https://__testlooper_server_and_port__/python-2.7.14.amd64.msi"
    $python_msi_file = "C:\ProgramData\TestLooper\python-2.7.14.amd64.msi"

    $client.DownloadFile($python_msi_url, $python_msi_file)
    Start-Process "msiexec.exe" -ArgumentList @("/i", $python_msi_file, 'ALLUSERS="1"', "/passive", "/quiet", "/L*V", "C:\ProgramData\TestLooper\python-install.log") -Wait
    $env:Path += ";C:\Python27;C:\Python27\Scripts"

    #get pip
    log("Installing pip and our dependencies")
    $client.DownloadFile("https://__testlooper_server_and_port__/get-pip.py", "C:\ProgramData\TestLooper\get-pip.py")
    python "C:\ProgramData\TestLooper\get-pip.py"
    pip install simplejson==3.13.2 requests==2.18.4 pyyaml==3.12 boto3==1.5.8 pyodbc==4.0.21 psutil==5.4.3 pypiwin32

    log("Installing git for windows")

    #download and install git 2.15.1
    $git_for_windows_url = "https://__testlooper_server_and_port__/Git-2.15.1.2-64-bit.exe"
    $git_for_windows_file = "C:/ProgramData/TestLooper/git_installer.exe"
    $client.DownloadFile($git_for_windows_url, $git_for_windows_file)

    Start-Process $git_for_windows_file -ArgumentList @("/silent", "/suppressmsgboxes", "/norestart", '/Dir="C:\Git"') -Wait
    $env:Path += ";C:\Git\bin"

    echo "" >> "C:\ProgramData\TestLooper\PreWorkerStartup.ps1"

    log("Writing startup.bat")
    # >> C:\ProgramData\TestLooper\RebootScript.log 2>&1 
    echo "Powershell -ExecutionPolicy Unrestricted C:\ProgramData\TestLooper\RebootScript.ps1" `
        | Out-File -FilePath "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp\startup.bat" -Encoding ASCII

    log("Executing startup script")
    C:\ProgramData\TestLooper\InstallScript.ps1 >> C:\ProgramData\TestLooper\InstallScript.log 2>&1

    if ($LASTEXITCODE -eq 0) {
        Write-S3Object -ContentType "application/octet-stream" -BucketName "__bootstrap_bucket__" `
            -Key "__bootstrap_log_key__.success"  -File C:\ProgramData\TestLooper\InstallScript.log
    }
    else {
        Write-S3Object -ContentType "application/octet-stream" -BucketName "__bootstrap_bucket__" `
            -Key "__bootstrap_log_key__.fail"  -File C:\ProgramData\TestLooper\InstallScript.log
    }
    
    Stop-Computer

} catch {
    log("Failed due to exception. Writing message to S3 at __bootstrap_log_key__")
    Write-S3Object -ContentType "application/octet-stream" -BucketName "__bootstrap_bucket__" -Key "__bootstrap_log_key__.fail"  -Content $_
}
</powershell>
