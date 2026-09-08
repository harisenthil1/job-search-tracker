Option Explicit
Dim http, msg
On Error Resume Next
Set http = CreateObject("MSXML2.XMLHTTP")
http.Open "GET", "http://127.0.0.1:8765/api/state", False
http.Send
If Err.Number = 0 And http.Status = 200 Then
    msg = "Search is running on http://127.0.0.1:8765"
Else
    msg = "Search is not running."
End If
On Error GoTo 0
MsgBox msg, vbInformation, "Search"
