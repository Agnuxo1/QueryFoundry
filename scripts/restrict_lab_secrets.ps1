$ErrorActionPreference = 'Stop'
$runtimeRoot = Join-Path (Split-Path -Parent $PSScriptRoot) '.runtime'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
foreach ($name in @('ssh-key','docker.env')) {
    $path = Join-Path $runtimeRoot $name
    if (Test-Path -LiteralPath $path) {
        $security = New-Object System.Security.AccessControl.FileSecurity
        $security.SetOwner($identity.User)
        $security.SetAccessRuleProtection($true, $false)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($identity.User, 'FullControl', 'Allow')
        $security.AddAccessRule($rule)
        Set-Acl -LiteralPath $path -AclObject $security
    }
}
Write-Output 'Generated laboratory secrets are restricted to the current Windows user.'
