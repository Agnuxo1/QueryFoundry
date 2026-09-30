$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$repairRoot = Join-Path $projectRoot '.runtime\wsl-repair'
$download = Get-Content -LiteralPath (Join-Path $repairRoot 'download.json') -Raw | ConvertFrom-Json
$signature = Get-AuthenticodeSignature -LiteralPath $download.file
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
    throw 'Official Microsoft signature validation failed'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden
    Write-Output 'Authorized repair launched; Windows may show its administrator prompt.'
    exit
}
try {
    $settingsPath = 'C:\Users\Windows-500GB\AppData\Roaming\Docker\settings-store.json'
    if (Test-Path -LiteralPath $settingsPath) {
        Copy-Item -LiteralPath $settingsPath -Destination (Join-Path $repairRoot 'docker-settings-before.json')
    }
    $installerLog = Join-Path $repairRoot 'wsl-msi.log'
    $arguments = '/i "' + $download.file + '" /qn /norestart /L*v "' + $installerLog + '"'
    $process = Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    $result = @{exit_code=$process.ExitCode; reboot_required=($process.ExitCode -eq 3010); service_binary_exists=(Test-Path 'C:\Program Files\WSL\wslservice.exe')}
    $result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $repairRoot 'install-result.json') -Encoding UTF8
} catch {
    @{error_type=$_.Exception.GetType().Name; phase='wsl_msi_install'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $repairRoot 'install-result.json') -Encoding UTF8
    throw
}
