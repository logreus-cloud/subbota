"""Оценка риска шелл-команд для режима «подтверждать только опасное».

Команда без признаков опасности выполняется сразу: запуск программ, поиск
файлов, чтение, закрытие обычных процессов. Подтверждение нужно для удаления,
записи, загрузок из сети, повышения прав, установки, системных настроек и
всего, что похоже на обфускацию (её не разобрать по тексту, значит — спросить).

Это защита от ошибок модели и простых инъекций, а не песочница: поэтому
категории «сеть», «исполнение» и «права» подтверждаются только в панели.
"""
from __future__ import annotations

import re

# Категории, которые нельзя подтвердить голосом: на слух не проверить, что именно
# скачается или выполнится.
PANEL_ONLY = {"сеть", "исполнение", "права"}

_PS_RULES: list[tuple[str, str]] = [
    ("удаление", r"remove-\w+|rm|del|erase|rd|rmdir|ri|clear-content|clc|clear-item|cli|"
                 r"clear-recyclebin|format-volume|clear-disk|initialize-disk|diskpart|format|cipher|sdelete|"
                 r"-delete|shred|unlink|truncate|srm|wipe"),
    ("запись файлов", r"set-content|add-content|ac|out-file|tee-object|tee|new-item|ni|md|mkdir|"
                      r"copy-item|copy|cp|cpi|move-item|move|mv|mi|rename-item|ren|rni|set-item|si|"
                      r"new-itemproperty|set-itemproperty|sp|export-\w+|compress-archive|expand-archive|sc|"
                      r"dd|touch|ln|chmod|chown|rsync|patch|sed\s+(?:-\w*i|--in-place)"),
    ("сеть", r"invoke-webrequest|iwr|invoke-restmethod|irm|curl|wget|start-bitstransfer|"
             r"bitsadmin|certutil|downloadstring|downloadfile|net\.webclient|webclient|send-mailmessage|"
             r"httpclient|system\.net\.\w+|tcpclient|udpclient|ssh|scp|sftp|ftp|tftp|telnet|ncat|netcat|"
             r"test-netconnection|resolve-dnsname|nslookup"),
    ("исполнение", r"invoke-expression|iex|invoke-command|icm|add-type|frombase64string|"
                   r"-encodedcommand|-enc|-ec|scriptblock|powershell|pwsh|cmd|wscript|cscript|mshta|"
                   r"rundll32|regsvr32|start-job|set-alias|new-alias|invoke-item|ii|import-module|ipmo|unblock-file|"
                   r"invoke-cimmethod|invoke-wmimethod|icim|iwmi|get-wmiobject|gwmi|set-ciminstance|"
                   r"new-ciminstance|set-wmiinstance|(?:python3?|py|node|perl|ruby|php|lua)\s+-[ce]"),
    ("права", r"runas|-verb\s+runas|set-acl|takeown|icacls|attrib"),
    ("установка", r"winget|choco|scoop|msiexec|install-\w+|uninstall-\w+|pip|npm|update-module"),
    ("система", r"set-executionpolicy|stop-computer|restart-computer|shutdown|logoff|bcdedit|bcdboot|"
                r"reg|regedit|set-service|new-service|stop-service|start-service|restart-service|"
                r"suspend-service|sc\.exe|netsh|set-net\w*|new-net\w*|remove-net\w*|disable-\w+|enable-\w+|"
                r"set-mppreference|add-mppreference|vssadmin|schtasks|register-scheduledtask|"
                r"unregister-scheduledtask|set-date|set-timezone|wmic|mountvol|set-culture"),
    ("git", r"git\s+(?:push|reset|clean|checkout|switch|rebase|merge|commit|rm|mv|restore|stash|"
            r"branch\s+-[dDmM]|remote|config|tag\s+-d)"),
]
_PS_RE = [(category, re.compile(rf"(?<![\w.-])(?:{pattern})(?![\w-])", re.IGNORECASE))
          for category, pattern in _PS_RULES]
# Системные процессы, которые нельзя завершать без спроса.
_CRITICAL = re.compile(
    r"(?<![\w-])(?:csrss|wininit|winlogon|lsass|services|svchost|smss|dwm|explorer|msmpeng|system)(?![\w-])",
    re.IGNORECASE,
)
# Вызовы, которые фильтр по именам командлетов не видит.
_EXEC_PATTERNS = [
    # Статические .NET-вызовы кроме заведомо чистых классов ([IO.File]::Delete, HttpClient…).
    re.compile(r"\[(?!\s*(?:system\.)?(?:math|string|datetime|convert|environment|guid|timespan|int\d*|"
               r"double|decimal|bool|regex|io\.path|text\.encoding)\s*\])[^\]]*\]\s*::", re.IGNORECASE),
    # Опасные методы объектов: $_.Delete(), (Get-Item a).MoveTo(…), $p.Kill().
    re.compile(r"\.\s*(?:delete\w*|moveto|copyto|replace|kill|terminate|create\w*|appendtext|writeall\w*|"
               r"write\w*|setaccesscontrol|encrypt|decrypt|invoke\w*|start)\s*\(", re.IGNORECASE),
    # ForEach-Object/% с именем метода без скобок: Get-Item a | % Delete.
    re.compile(r"(?:%|foreach-object)\s+(?!\{)[a-z]\w*", re.IGNORECASE),
    # Dot-вызов собранной строки: .('Rem'+'ove-Item').
    re.compile(r"(?:^|[;|{(\s])\.\s*[($'\"]"),
    # Запуск скриптов и установщиков.
    re.compile(r"\.(?:ps1|psm1|bat|cmd|vbs|vbe|js|jse|wsf|hta|msi|msp|scr|reg|jar)\b", re.IGNORECASE),
]
# Завершить все процессы подряд: Get-Process | Stop-Process.
_KILL_ALL = re.compile(r"(?<![\w-])(?:get-process|gps|ps)\s*\|\s*(?:stop-process|spps|kill)(?![\w-])", re.IGNORECASE)
_KILL = re.compile(r"(?<![\w-])(?:stop-process|spps|kill|taskkill)(?![\w-])", re.IGNORECASE)
# Перенаправление в $null безопасно и встречается постоянно.
_NULL_REDIRECT = re.compile(r"\d?>\s*\$null|\d?>&\d|\|\s*out-null", re.IGNORECASE)
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def shell_risk(command: str, shell: str = "PowerShell") -> str | None:
    """Категория риска команды или None, если команду можно выполнить без спроса."""
    text = command.strip()
    if not text:
        return "исполнение"
    if shell == "Bash":
        # Git Bash: кавычки, $ и обратный слеш позволяют собрать любое слово
        # (r''m, \rm, $(…)), по тексту это не проверить.
        if re.search(r"[\\'\"`$]|\beval\b|\b(?:ba|z)?sh\s+-c\b", text):
            return "исполнение"
    else:
        # PowerShell: обратная кавычка экранирует, & ( / & $ вызывают собранную строку.
        if "`" in text or re.search(r"&\s*[($]|\.invoke\s*\(|\[char\]|-join\s*\(", text, re.IGNORECASE):
            return "исполнение"
    stripped = _NULL_REDIRECT.sub(" ", text)
    if ">" in _QUOTED.sub(" ", stripped):
        return "запись файлов"
    for regex in _EXEC_PATTERNS:
        if regex.search(stripped):
            return "исполнение"
    if _KILL_ALL.search(stripped):
        return "система"
    for category, regex in _PS_RE:
        if regex.search(stripped):
            return category
    if _KILL.search(stripped) and _CRITICAL.search(stripped):
        return "система"
    return None
