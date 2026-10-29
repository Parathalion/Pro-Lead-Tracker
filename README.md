# Pro Lead Tracker

An installable app for tracking DFW commercial construction leads. Every Monday at 4am Central, a GitHub job checks Frisco's and Murphy's monthly permit lists. The app adds any new projects and contractors the next time you open it. Claude isn't needed for this part.

- **App:** `index.html`, `manifest.webmanifest`, `sw.js`, `icons/`
- **Weekly job:** `.github/workflows/weekly-permits.yml` runs `scraper/fetch_permits.py`
- **Data the app reads:** `data/permits.json` (permits and contractors, written by the job) and `data/contacts-extra.json` (phone numbers from Claude, uploaded by you)

Your notes, stages, quotes, and the leads you edit stay on your phone. Only public permit records go in this project.

---

## One-time setup (about 15 minutes, on a computer)

### 1. Create the project on GitHub
1. Sign in at **github.com**, click **+** (top right), then **New repository**.
2. Name it `pro-lead-tracker`. Choose **Public**, which free GitHub Pages hosting needs. Leave "Add a README" unchecked. Click **Create repository**.

### 2. Upload the files
1. Unzip `pro-lead-tracker-repo.zip`.
2. On the new repository page, click **uploading an existing file**.
3. Open the unzipped `pro-lead-tracker-repo` folder, select **everything inside it**, and drag it onto the page. That includes the `data`, `icons`, `scraper` and `.github` folders.
   - **Mac:** `.github` is hidden in Finder. Press **Cmd + Shift + .** to show it.
   - **Windows:** turn on View → Show → Hidden items if you don't see it.
4. Click **Commit changes**.
5. Check that the repository now shows a `.github` folder. If it doesn't, click **Add file → Create new file**, type `.github/workflows/weekly-permits.yml` as the name, paste in the contents of that file from the zip, and commit.

### 3. Let the weekly job save its results
**Settings → Actions → General →** under "Workflow permissions" choose **Read and write permissions → Save**.

### 4. Turn on the website
**Settings → Pages →** Source: **Deploy from a branch**, Branch: **main**, folder **/ (root)** → **Save**.
After a minute or two your app is live at:
`https://YOUR-GITHUB-NAME.github.io/pro-lead-tracker/`

### 5. Do a first test run
1. Open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
2. Click **Weekly permit check → Run workflow → Run workflow**.
3. Wait about a minute. A green check means it worked.
   - The **Self-check** step re-reads the real September 2026 permit lists and compares them with the permits already confirmed by hand. That shows the job can read the cities' real files.
   - A red ✗ means something needs a fix. Open the run and read the lines under **NEEDS ATTENTION** or **SELF-TEST FAILED**. Download **debug-text** at the bottom of the run page and give it to Claude to fix the parser.

### 6. Install on your phone
Open your `github.io` address on your phone:
- **iPhone (Safari):** tap Share, then **Add to Home Screen**.
- **Android (Chrome):** tap **Install app**, or the ⋮ menu and then **Install app**.

The first time it opens, it loads all the leads collected so far: 134 projects and 270 contacts.

---

## Every week (automatic)
- The job runs Monday around 4am Central. GitHub can start scheduled jobs a little late.
- New permits are saved to `data/permits.json`. When you open the app, a banner like "Added 8 new projects, 5 new contacts…" appears.
- The app never brings back a record you deleted, and never overwrites notes you've edited.
- **GitHub emails you if a run fails,** for example if a city changes its permit list layout, moves the page, or goes 3 months without posting.

## Phone numbers (from the Claude task)
Claude's Monday task keeps looking up phone numbers in your claude.ai tracker. To get them into the app:
1. In the claude.ai tracker, open **Contacts** and tap **Send phones to app**. This saves `contacts-extra.json`.
2. In GitHub, open the **data** folder, click **Add file → Upload files**, drop in `contacts-extra.json`, then **Commit changes**. This replaces the old file.
3. Every device with the app fills in blank phone numbers and emails the next time it opens.

To update just one phone instead, open the app, tap **Restore**, and pick the `contacts-extra.json` file.

## Back up
Your edits live on each device. Tap **Back up** in the app now and then. If you uninstall the app or clear your browser's site data, what's saved on that device is erased.

## Run it now / change the time
- **Run now:** Actions → Weekly permit check → Run workflow.
- **Change the day or time:** edit the `cron:` lines in `.github/workflows/weekly-permits.yml`. GitHub uses UTC: 4am Central is 09:00 UTC in summer and 10:00 UTC in winter.

## For developers
```
pip install -r scraper/requirements.txt
python scraper/test_parsers.py          # offline parser tests
python scraper/fetch_permits.py --selftest
python scraper/fetch_permits.py --dry-run
```
