Set sh = CreateObject("WScript.Shell")
code = sh.Run("cmd.exe /c ""D:\workbudy\work\ops-agent-delivery\code\backend\scripts\sync_prometheus_alerts.cmd""", 0, True)
WScript.Quit code