
# Job Search Tracker

This tool was built around one theory, that finding a job is primarily about demand and supply. When you are not georgraphically limited, it is very difficult to sort through jobs in Linkedin for the entire country.

The tool uses a bucket system. It can support any number of buckets. You set a percentage for each bucket so you decide how much time you spend on each bucket. You put locations in a bucket. Here I user Metro Statisctical Areas (MSA) so I am not looking at cities but economical areas.

The theory is that you'd be able to apply to jobs with less than 100 postings on linkedin, across the country, in locations no one is looking for. Companies far from big cities are desperate for talent. This is already possible with Linkedin search filter, but it can be annoying to set location to "United States" and only have jobs from multi-billion-dollar companies that were posted in the last 24 hours.



## Set up
Download the repository. (green code button -> download zip - unzip) The csv files (a.csv, b.csv, c.csv) in the data folder are made from my personal preference (a.csv) and software/computer engineering job opportunities opportunity score (b.csv, c.csv). You are free to modify them.



### First run
1. Put your bucket CSV files in storage\data\ (for example a.csv, b.csv, c.csv).
2. Double-click setup.bat once.
3. Double-click Start Search.vbs.

### Normal use
- Start `Search.vbs` starts/reuses one hidden backend, starts/reuses one overlay, and opens the website.
- Closing the browser does not stop Search.
- Clicking `X` on the overlay closes the overlay and gracefully shuts down the backend.
- Stop `Search.vbs` is a backup way to stop the backend.
- Server `Status.vbs` tells you whether the backend is alive.
- Run `Debug.bat` is a visible-console fallback for troubleshooting.

## Storage
Everything you own/preserve lives under storage\:
```
  storage\data\       bucket CSV files
  storage\database\   SQLite database + tiny runtime pid/settings files
  storage\exports\    exported analysis CSVs
  storage\logs\       server logs
  ```

## Community
I welcome improvements for this site, any bug reports or pull request would help me and other make their job search more effective.
