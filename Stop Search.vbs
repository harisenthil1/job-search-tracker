Option Explicit
Dim http
On Error Resume Next
Set http = CreateObject("MSXML2.XMLHTTP")
http.Open "POST", "http://127.0.0.1:8765/api/shutdown", False
http.setRequestHeader "Content-Type", "application/json"
http.Send "{}"
On Error GoTo 0
