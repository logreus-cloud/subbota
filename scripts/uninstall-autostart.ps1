$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName 'SubbotaAssistant' -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host 'Автозапуск Субботы не установлен.'
    return
}
Stop-ScheduledTask -TaskName 'SubbotaAssistant' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'SubbotaAssistant' -Confirm:$false
Write-Host 'Автозапуск Субботы удалён.'
