# Run locally as the same Windows user that will run the scheduled task.
# Passwords are prompted securely, never supplied as command arguments.
$ErrorActionPreference = 'Stop'
$certSecret = Read-Host 'Certificate password' -AsSecureString

function Save-CybosSecret([string]$Name, [Security.SecureString]$Secret) {
    if ($Secret.Length -eq 0) { throw "$Name is required." }
    $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    try {
        $plainValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
        [Environment]::SetEnvironmentVariable($Name, $plainValue, 'User')
        [Environment]::SetEnvironmentVariable($Name, $plainValue, 'Process')
    } finally {
        $plainValue = $null
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
    }
}

try {
    if ($certSecret.Length -eq 0) { throw 'Certificate password is required.' }
    Save-CybosSecret 'CYBOS_CERT_PASSWORD' $certSecret
    Write-Host 'Saved CYBOS_CERT_PASSWORD in the local user environment. ID and login password are not used.'
} finally {
    $certSecret.Dispose()
}
