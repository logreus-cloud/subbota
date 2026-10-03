$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName 'JarvisAssistant' -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host 'Автозапуск Джарвиса не установлен.'
    return
}
Stop-ScheduledTask -TaskName 'JarvisAssistant' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'JarvisAssistant' -Confirm:$false
Write-Host 'Автозапуск Джарвиса удалён.'
