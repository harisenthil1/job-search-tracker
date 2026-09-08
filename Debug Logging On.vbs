Option Explicit
Dim fso, root, logDir, flag, file
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
If Not fso.FolderExists(root & "\storage") Then fso.CreateFolder(root & "\storage")
logDir = root & "\storage\logs"
If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)
flag = logDir & "\debug.flag"
Set file = fso.CreateTextFile(flag, True)
file.WriteLine "enabled"
file.Close
MsgBox "Detailed request logging enabled. Restart Search for it to take effect.", vbInformation, "Search"
