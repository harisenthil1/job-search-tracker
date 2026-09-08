Option Explicit
Dim fso, root, flag
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
flag = root & "\storage\logs\debug.flag"
If fso.FileExists(flag) Then fso.DeleteFile flag, True
MsgBox "Detailed request logging disabled. Restart Search for it to take effect.", vbInformation, "Search"
