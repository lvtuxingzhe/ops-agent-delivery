Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
cmdPath = fso.BuildPath(scriptDir, "sync_prometheus_alerts.cmd")
code = sh.Run("cmd.exe /c """ & cmdPath & """", 0, True)
WScript.Quit code