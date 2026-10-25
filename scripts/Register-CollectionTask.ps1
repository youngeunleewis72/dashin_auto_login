[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')]
    [string]$At,
    [string]$TaskName = 'Daishin-CYBOS-Collect',
    [string]$Batch = 'C:\myprojects\study\kr_stocks_dashin_collector\run_schedule.bat',
    [string]$Executable,
    [string]$LogDirectory,
    [switch]$Preview,
    [switch]$Update
)
$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $Executable) { $Executable = Join-Path $projectDirectory 'target\i686-pc-windows-msvc\release\dashin-auto-login.exe' }
if (-not $LogDirectory) { $LogDirectory = Join-Path $projectDirectory 'logs' }
$Executable = (Resolve-Path -LiteralPath $Executable).Path
$Batch = (Resolve-Path -LiteralPath $Batch).Path
$LogDirectory = [IO.Path]::GetFullPath($LogDirectory)
if ([IO.Path]::GetExtension($Executable) -ne '.exe') { throw 'Executable must be an .exe file.' }
if ([IO.Path]::GetExtension($Batch) -ne '.bat') { throw 'Batch must be a .bat file.' }
if ($TaskName.IndexOfAny([char[]]'\/') -ge 0 -or [string]::IsNullOrWhiteSpace($TaskName)) {
    throw 'TaskName must be a non-empty name without path separators.'
}

function Quote-NativeArgument([string]$Value) {
    if ($Value -match '["\r\n]') { throw 'Paths must not contain quotes or newlines.' }
    # Windows argv quoting: double trailing backslashes before the closing quote.
    return '"' + ($Value -replace '(\\+)$', '$1$1') + '"'
}
function Xml-Value([string]$Value) { return [Security.SecurityElement]::Escape($Value) }

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$userSid = $identity.User.Value
$parts = $At.Split(':')
$nextRun = [DateTime]::Today.AddHours([int]$parts[0]).AddMinutes([int]$parts[1])
while ($nextRun -le [DateTime]::Now -or $nextRun.DayOfWeek -in @('Saturday', 'Sunday')) {
    $nextRun = $nextRun.AddDays(1)
}
$arguments = '--log-dir ' + (Quote-NativeArgument $LogDirectory) + ' run --batch ' + (Quote-NativeArgument $Batch)
$description = 'Managed by dashin_auto_login/Register-CollectionTask.ps1; certificate login, quote verification, collection. v1'
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>$(Xml-Value $description)</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>$($nextRun.ToString('yyyy-MM-ddTHH:mm:ss'))</StartBoundary><Enabled>true</Enabled>
    <ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek><Monday/><Tuesday/><Wednesday/><Thursday/><Friday/></DaysOfWeek></ScheduleByWeek>
  </CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>$(Xml-Value $userSid)</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable><Enabled>true</Enabled><WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit></Settings>
  <Actions Context="Author"><Exec><Command>$(Xml-Value $Executable)</Command><Arguments>$(Xml-Value $arguments)</Arguments><WorkingDirectory>$(Xml-Value $projectDirectory)</WorkingDirectory></Exec></Actions>
</Task>
"@
if ($Preview) {
    $previewDirectory = Join-Path $projectDirectory 'target'
    New-Item -ItemType Directory -Path $previewDirectory -Force | Out-Null
    $previewFile = Join-Path $previewDirectory 'collection-task-preview.xml'
    [IO.File]::WriteAllText($previewFile, $xml, [Text.Encoding]::Unicode)
    Write-Host "Preview only. No task registered. XML: $previewFile"
    Write-Host "Weekdays at $At (PC local time); first eligible boundary: $nextRun"
    return
}
if (-not $PSCmdlet.ShouldProcess($TaskName, "Register weekday collection at $At")) { return }
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script in an elevated PowerShell as the same user that owns CYBOS credentials.'
}
# Query all root tasks so a lookup/permission failure cannot be mistaken for absence.
$existing = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object TaskName -eq $TaskName)
if ($existing.Count -gt 0) {
    if (-not $Update) { throw "$TaskName already exists. Inspect it, then use -Update if appropriate." }
    if ($existing[0].Description -ne $description) { throw 'Refusing to replace a task not managed by this script.' }
    if ($existing[0].State -eq 'Running') { throw 'Refusing to replace a running task.' }
}
Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -Xml $xml -Force | Out-Null
$registered = Get-ScheduledTask -TaskName $TaskName -TaskPath '\'
if ($registered.Principal.LogonType -ne 'Interactive' -or $registered.Principal.RunLevel -ne 'Highest') {
    throw 'Registered task does not match required interactive/highest privileges. Inspect the task.'
}
$info = Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath '\'
Write-Host "Registered $TaskName; weekdays at $At. No collection was started."
Write-Host "Next run: $($info.NextRunTime); log directory: $LogDirectory"
