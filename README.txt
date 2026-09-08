SEARCH
======

Fresh local build.

FIRST RUN
1. Put your bucket CSV files in storage\data\ (for example a.csv, b.csv, c.csv).
2. Double-click setup.bat once.
3. Double-click Start Search.vbs.

NORMAL USE
- Start Search.vbs starts/reuses one hidden backend, starts/reuses one overlay, and opens the website.
- Closing the browser does not stop Search.
- Clicking X on the overlay closes the overlay and gracefully shuts down the backend.
- Stop Search.vbs is a backup way to stop the backend.
- Server Status.vbs tells you whether the backend is alive.
- Run Debug.bat is a visible-console fallback for troubleshooting.

STORAGE
Everything you own/preserve lives under storage\:
  storage\data\       bucket CSV files
  storage\database\   SQLite database + tiny runtime pid/settings files
  storage\exports\    exported analysis CSVs
  storage\logs\       server logs

The ZIP intentionally contains no database and no CSV data.

CODE
All application code, HTML, CSS, JavaScript, and the local virtual environment created by setup.bat live under code\.

SERVER
http://127.0.0.1:8765
