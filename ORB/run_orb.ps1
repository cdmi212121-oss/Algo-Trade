# Wrapper invoked by the "ORB Opening Range Breakout" Windows Scheduled
# Task at 9:30 AM on weekdays. Uses Start-Process's own redirection (not
# PowerShell's `*>>`/`2>&1` on a native exe) - the latter wraps every
# stderr line as a NativeCommandError object and writes UTF-16, corrupting
# a plain log file. Start-Process redirects the raw OS file handles instead.
$orbDir = "C:\Users\LENOVO\Downloads\GTT Scaping\ORB"
New-Item -ItemType Directory -Force -Path "$orbDir\logs" | Out-Null
$day = Get-Date -Format "yyyyMMdd"
$stdout = "$orbDir\logs\run_$day.log"
$stderr = "$orbDir\logs\run_$day.err.log"

$proc = Start-Process -FilePath "C:\Users\LENOVO\AppData\Local\Programs\Python\Python311\python.exe" `
    -ArgumentList "main.py" -WorkingDirectory $orbDir `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
    -NoNewWindow -Wait -PassThru
exit $proc.ExitCode
