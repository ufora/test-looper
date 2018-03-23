<powershell>

########################################################################
#this is a bootstrap script to initialize a Windows test-looper worker.
#We install ssh keys, and then pull powershell instructions
#from a specified bucket/key pair. 
########################################################################

try {
    md -Force C:\ProgramData\TestLooper
    md -Force C:\Users\Administrator\.ssh

    #fixup the hosts file.
    __hosts__

    #export our ssh keys so 'git' can find them
    echo "__test_key__" | Out-File -FilePath "C:\Users\Administrator\.ssh\id_rsa" -Encoding ASCII
    echo "__test_key_pub__" | Out-File -FilePath "C:\Users\Administrator\.ssh\id_rsa.pub" -Encoding ASCII
    echo "StrictHostKeyChecking=no" | Out-File -FilePath "C:\Users\Administrator\.ssh\config" -Encoding ASCII

    Read-S3Object -BucketName __bootstrap_bucket__ -Key __bootstrap_key__  -File C:\ProgramData\TestLooper\SetupBootstrap.ps1
    Remove-S3Object -Force -BucketName __bootstrap_bucket__ -Key __bootstrap_key__

    Set-ItemProperty -Path “HKLM:SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon” -Name autoadminlogon -Type DWORD -Value 1

    "Writing startup.bat"
    echo "Powershell -ExecutionPolicy Unrestricted C:\ProgramData\TestLooper\SetupBootstrap.ps1 >> C:\ProgramData\TestLooper\SetupBootstrap.log 2>&1 " `
        | Out-File -FilePath "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp\startup.bat" -Encoding ASCII

    "Rebooting the machine!"
    Restart-Computer

} catch {
    Write-S3Object -ContentType "application/octet-stream" -BucketName "__bootstrap_bucket__" -Key "__bootstrap_log_key__"  -Content $_
}
</powershell>
