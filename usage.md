# Scrapz: from clone to your first collection

This guide takes you from a fresh clone to a completed collection job. Scrapz collects from URLs you supply and public webpages or RSS/Atom feeds you register; it does not search the web for sources automatically.

## 1. Clone the repository

Install Git and Python 3.11 or newer, then open a terminal:

```bash
git clone https://github.com/itscliffordonyango/Scrapz.git
cd Scrapz
```

## 2. Create an environment and install dependencies

On Linux or macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell prevents environment activation, use a terminal where activation is permitted or run the environment's Python directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. Create your administrator account

From the repository directory, run:

```bash
python -m app.cli create-user --username admin --role admin
```

Enter and confirm a password when prompted. Use a unique password of at least 12 characters. Scrapz does not ship with a default account or password.

## 4. Start Scrapz and sign in

```bash
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> in your browser and sign in with the account you just created. Keep the terminal running while you use the app; it also runs the collection worker. Stop it with **Ctrl+C** when finished.

On first startup, Scrapz creates `.state/` with its SQLite database and session-signing key. Keep this directory private and back it up if you need to preserve your records and accounts. Do not commit it to source control.

## 5. Choose a permitted public source

Find a public webpage or RSS/Atom feed that you are authorized to collect from. Before adding it, check the publisher's terms, privacy requirements, and `robots.txt` policy. Scrapz will also check `robots.txt` when it runs; a job will not fetch a page when permission is denied or the policy cannot be checked.

For a first run, a public RSS/Atom feed is often useful because one successful job can produce several records. A feed run examines at most 30 entries. A direct URL run fetches one webpage; it does not crawl links from that page.

Scrapz only supports public HTTP(S) URLs on their standard ports. It does not sign in to sites, bypass paywalls or CAPTCHAs, or evade access controls.

## 6. Register a feed or webpage (administrator)

1. Select **Sources** in the navigation.
2. Under **Add a source**, enter a display name and its public URL.
3. Choose **RSS / Atom feed** for a feed URL, or **Single webpage** for one HTML page.
4. Choose a default classification and select **Add source**.
5. Confirm the source appears as **Enabled** in the configured sources list.

Use the direct-URL option in the next step if you prefer not to register a source. Analysts can run jobs but only administrators can manage the registry.

## 7. Queue your first collection job

1. Return to **Overview**.
2. In **Start a job**, either choose the registered source **or** enter one public URL. Do not fill in both.
3. Optionally enter comma-separated search terms, such as `water quality, watershed`.
   - Terms are treated as **OR** matches against each result's title and excerpt.
   - For a feed, entries that match none of the terms are omitted. For a webpage, a non-matching page produces no record.
   - Leave the field empty to keep all parsed feed entries or the single webpage record.
4. Select **Queue collection**.

The new job appears under **Recent collection jobs**. Its status moves from **queued** to **running**, then to **completed** or **failed**. Keep the app running while it processes jobs. Scrapz spaces requests to the same host and may retry temporary failures.

## 8. Review the results

When the job completes:

1. Open **Records** or select **Browse records**.
2. Confirm records show a title, classification, source link, collection date, and extraction confidence.
3. Open the source link and compare the record with the original page and context.
4. If you are an admin or analyst, set the review state to **Source checked** or **Uncertain** as appropriate. These are review notes, not proof that a claim is true.
5. Use the filters to find records and **Export CSV** to download the current results.
6. Open **Analytics** to review collection volume and classifications. Administrators can also see audit events.

Extraction confidence measures how much useful metadata was found; it is not a credibility or truth score. Exact duplicate URL/content pairs are skipped, so a repeat run may complete with zero new records.

## If a job does not produce records

- **Still queued:** Make sure the Scrapz server is still running. Jobs are processed by its in-process worker.
- **Failed:** Check the job's error on **Overview**. Confirm that the URL is correct and public, the host resolves to a public address, and the server can reach it. Scrapz may reject non-standard ports, oversized responses, unsupported content types, or too many redirects.
- **Robots policy denied or unavailable:** Do not try to work around it. Choose a different source whose policy permits collection.
- **Completed with zero records:** For a webpage, verify that it has extractable HTML title or body content and that your optional search terms match the title or excerpt. For a feed, verify that it is a valid RSS/Atom feed and that matching terms occur in entry titles or summaries. Try an empty search field to test whether terms were filtering out the results.
- **Source already registered:** Reuse the enabled source in the collection form, or choose another URL.

## Optional: add analyst or viewer accounts

Stop the server with **Ctrl+C**, then create an account from the repository directory:

```bash
python -m app.cli create-user --username analyst1 --role analyst
```

Choose `analyst` to create jobs and review records, or `viewer` for read-only access. Restart the server with `uvicorn app.main:app --reload` afterward.

For configuration and deployment notes, see [README.md](./README.md).
