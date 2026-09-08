
# Job Search Tracker

This tool was built around one theory, that finding a job is primarily about demand and supply. When you are not geographically limited, it is very difficult to sort through jobs in LinkedIn for the entire country.

The tool uses a bucket system. It can support any number of buckets. You set a percentage for each bucket so you decide how much time you spend on each bucket. You put locations in a bucket. Here I use Metropolitan Statistical Areas (MSA) so I am not looking at cities but economical areas.

The theory is that you'd be able to apply to jobs with less than 100 postings on linkedin, across the country, in locations no one is looking for. Companies far from big cities are desperate for talent. This is already possible with Linkedin search filter, but it can be annoying to set location to "United States" and only have jobs from multi-billion-dollar companies that were posted in the last 24 hours. You still have to manually use the filter to change the location, but the tracker makes it less tedious (we're dealing with 200+ MSAs here) so every week you've gone though almost all small location nobody bothers to look up  and the companies struggle to get people to move there.

<figure>
  <img alt="app demo" src="https://github.com/user-attachments/assets/d2e64604-6b8c-4f63-9df6-acaa74385bd6">
  <figcaption><i>Screenshot: It also comes with a always-on-top helper software</i></figcaption>
</figure>


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
I welcome improvements for this site, any bug reports or pull request would help me and others make their job search more effective. The app collects data about time spent on each MSA and how many application you were able to make. It is stored locally and available for you to export. My theory is that after enough weeks I'll enough data to create more buckets and strategically prioritize location that are mildly undesirable to most, has low supply and more job posts.

I'm also working on a extension to make skipping through job posts you can't qualify. It uses my personal ranking score to also rate the metro area by political leaning, crime (unavailable for major metros), how affordable the place is and diversity.

<img alt="image" src="https://github.com/user-attachments/assets/12f82e2c-b7a4-4390-bf7d-b9518008ba12" />

I also downloaded H1B LCAs filed, E-verify data for all national employers and use that to locally match employers. It has a 95% success rate, you're also able to see a list of similar companies and match them manually. Clearance requirement tile works 99% of the time, sponsorship language is difficult to measure so it only works 70% of the time. I'm working on fixing that deterministically. This is currently a firefox extension only, I welcome any input!

