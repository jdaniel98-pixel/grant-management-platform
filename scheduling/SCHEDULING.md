# Scheduling the daily routine on a Mac (launchd)

Nothing here is installed or switched on for you. These are the steps to take yourself, when you choose to.

## What the daily routine does

`run-daily` runs these steps in order. A failing step never stops the next one, and everything is written to
`instance/logs/daily-YYYY-MM-DD.log`.

1. `discover` for each source you have **enabled** (none are enabled until you run `sources --enable`).
2. `housekeep`: closes grants whose recorded closing date has passed. Nothing else is ever closed automatically.
3. `match`: assesses new or changed grants (only if `OPENAI_API_KEY` is set; otherwise skipped and logged).
4. `digest`: weekly or daily alert digests to consented contacts.
5. `notify`: deadline reminders.

Digests and reminders go to `instance/outbox/` only, unless email delivery is fully set up (see `.env.example`). It is
off by default, and even when on, only addresses on `EMAIL_ALLOWLIST` can be emailed. Every step is safe to repeat
the same day: nothing is sent twice.

## Before you schedule it

Run each of these by hand first and read the output:

    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs email-status
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs discover find-a-grant --dry-run
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs run-daily

## Install

1. Copy the template and put in your real project folder (keep the quotes around paths with spaces):

       cp scheduling/com.brightpath.grantplatform.daily.plist ~/Library/LaunchAgents/
       open -e ~/Library/LaunchAgents/com.brightpath.grantplatform.daily.plist

   Replace every `/ABSOLUTE/PATH/TO/Grant Management Platform` with the output of `pwd` run in the project folder.
2. Create the log folder: `mkdir -p instance/logs`
3. Check the file is valid: `plutil -lint ~/Library/LaunchAgents/com.brightpath.grantplatform.daily.plist`
4. Load it: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.brightpath.grantplatform.daily.plist`
5. Try it once now: `launchctl kickstart gui/$(id -u)/com.brightpath.grantplatform.daily`, then read `instance/logs/`.

## Check, pause and remove

- Is it loaded? `launchctl print gui/$(id -u)/com.brightpath.grantplatform.daily | head`
- Pause it: `launchctl bootout gui/$(id -u)/com.brightpath.grantplatform.daily`
- Remove it: pause it, then `rm ~/Library/LaunchAgents/com.brightpath.grantplatform.daily.plist`
- Stop only grant fetching: `PYTHONPATH=src .venv/bin/python -m grant_platform.jobs sources --disable find-a-grant`

## Things to know

- launchd runs only while you are logged in (a LaunchAgent). If the Mac was off or asleep, the run happens at the next wake.
- macOS may ask for permission the first time a program in `Documents` or `Desktop` is run by launchd. If the log shows
  "Operation not permitted", grant the Python in `.venv` access under System Settings, Privacy & Security, or move the project.
- Source sites are fetched politely (a pause between requests and a page cap per run), so a daily run is light.
