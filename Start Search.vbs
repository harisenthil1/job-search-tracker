Option Explicit
Dim shell, fso, root, pyw, launcher, q
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
q = Chr(34)
root = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = root & "\code\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = "pythonw.exe"
launcher = root & "\code\launcher.py"
shell.CurrentDirectory = root
shell.Run q & pyw & q & " " & q & launcher & q, 0, False
