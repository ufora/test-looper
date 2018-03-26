<powershell>

########################################################################
#this is a bootstrap script to initialize a Windows test-looper worker.
#We install ssh keys, and then pull powershell instructions
#from a specified bucket/key pair. 
########################################################################

$machineId = Invoke-RestMethod -uri http://169.254.169.254/latest/meta-data/instance-id

$client = New-Object System.Net.WebClient

function log($msg) {
    $client.DownloadString("https://__testlooper_server_and_port__/machineHeartbeatMessage?machineId=" + $machineId + "&heartbeatmsg=" + [uri]::EscapeUriString($msg))  
}

try {

    md -Force C:\ProgramData\TestLooper
    md -Force C:\Users\Administrator\.ssh

    #fixup the hosts file.
    __hosts__

    log("Executing inital bootstrap script.")

    #export our ssh keys so 'git' can find them
    echo "__test_key__" | Out-File -FilePath "C:\Users\Administrator\.ssh\id_rsa" -Encoding ASCII
    echo "__test_key_pub__" | Out-File -FilePath "C:\Users\Administrator\.ssh\id_rsa.pub" -Encoding ASCII
    echo "StrictHostKeyChecking=no" | Out-File -FilePath "C:\Users\Administrator\.ssh\config" -Encoding ASCII

    Read-S3Object -BucketName __bootstrap_bucket__ -Key __bootstrap_key__  -File C:\ProgramData\TestLooper\SetupBootstrap.ps1
    Remove-S3Object -Force -BucketName __bootstrap_bucket__ -Key __bootstrap_key__

    $password = ([char[]]([char]33..[char]95) + ([char[]]([char]97..[char]126)) + 0..9 | sort {Get-Random})[0..16] -join ''
    $password = "t3stl00per_" + $password

    $secure_password = ConvertTo-SecureString $password -AsPlainText -Force

    Set-LocalUser -Name "Administrator" -Password $secure_password
    
    Set-ItemProperty -Path “HKLM:SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon” -Name DefaultPassword -Type STR -Value $password
    Set-ItemProperty -Path “HKLM:SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon” -Name DefaultUsername -Type STR -Value "Administrator"
    Set-ItemProperty -Path “HKLM:SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon” -Name DefaultDomainName -Type STR -Value "$env:USERDOMAIN"
    Set-ItemProperty -Path “HKLM:SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon” -Name AutoAdminLogon -Type DWORD -Value 1

    log("writing startup.bat")
    echo "Powershell -ExecutionPolicy Unrestricted C:\ProgramData\TestLooper\SetupBootstrap.ps1 >> C:\ProgramData\TestLooper\SetupBootstrap.log 2>&1 " `
        | Out-File -FilePath "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp\startup.bat" -Encoding ASCII

    $ip = (Get-NetIPConfiguration).IPv4Address.IPAddress

    log("Rebooting the machine. New password is $password and ip is $ip")
    Restart-Computer

} catch {
    log("Failed due to exception. Writing message to S3 at __bootstrap_log_key__")
    Write-S3Object -ContentType "application/octet-stream" -BucketName "__bootstrap_bucket__" -Key "__bootstrap_log_key__"  -Content $_
}
</powershell>
