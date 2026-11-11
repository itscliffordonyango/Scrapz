# Scrapz Research

Scrapz is a local, role-aware workspace for collecting and organizing public web material from explicit URLs and administrator-registered web pages or RSS/Atom feeds. It keeps source attribution, extraction confidence, timestamps, categories, and audit events with the resulting records.

For a step-by-step guide from cloning the repository to your first collection, see [usage.md](./usage.md).

## Requirements and setup

- Python 3.11 or newer
- Network access only for public source hosts that permit the configured user agent

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m app.cli create-user --username admin --role admin
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. The first startup creates a local `.state/` directory containing the SQLite database and a private session-signing key. Back up these files securely. To use a non-local database location, set `SCRAPZ_DATABASE_PATH`; to use an externally managed session key, set `SCRAPZ_SESSION_SECRET` to a random value of at least 32 characters. Set `SCRAPZ_SECURE_COOKIES=1` when serving behind HTTPS.

Create additional analyst or viewer accounts with `python -m app.cli create-user --username NAME --role analyst` (or `viewer`). No default account or password is provisioned.

## How collection works

- Analysts and admins can queue a single explicit URL or choose an enabled source in the registry. Admins manage the registry.
- Registered feeds are parsed as RSS/Atom; webpages are parsed for title, description, publication time, canonical URL, excerpt, and optional non-person named entities.
- Comma-separated search terms are matched as OR terms against each result's title and excerpt. Feed runs inspect up to 30 entries.
- A background worker processes jobs concurrently (default 3; set `SCRAPZ_WORKER_CONCURRENCY` from 1 to 16) and spaces requests to each host (default 1 second; configure `SCRAPZ_MIN_HOST_INTERVAL_SECONDS` from 0 to 60). Failed jobs retry up to three times with a short backoff.
- Exact URL/content duplicates are skipped. Records remain unverified; their confidence score reflects extraction metadata completeness only.
- Analysts/admins can set a record to “source checked” or “uncertain”; this review state is audited and never implies independent fact verification.
- Analytics, record filters, CSV export, and administrator audit history are available in the UI.

## Optional named-entity extraction

The base installation does not download language models. To enable NER, install spaCy and an appropriate model separately, then set `SCRAPZ_SPACY_MODEL` to that installed model name before starting the app. Only ORG, GPE, LOC, EVENT, and PRODUCT labels are retained; person labels are omitted. If a configured model cannot load, application startup fails with an explicit error.

## Collection and deployment safeguards

- Scrapz uses a descriptive user agent, checks each host's `robots.txt`, denies collection when its policy cannot be retrieved or parsed as allowed, and does not follow redirects without rechecking the destination.
- Only HTTP(S) on standard ports is accepted. Hostnames resolving to non-public IP addresses are rejected. Responses are capped at 2 MB (robots files at 100 KB), time out, and have a redirect limit.
- Scrapz does not authenticate to sources, bypass CAPTCHAs/paywalls, or evade access controls. Add only sources you are authorized to collect from and follow applicable terms, privacy requirements, and retention policies.
- This is a single-process local deployment with SQLite and an in-process durable queue. For multi-instance production use, replace SQLite and the queue with managed relational storage and a dedicated task broker/worker, configure HTTPS and secret management, and add deployment-specific backups, monitoring, retention, and access controls.

## Tests

```bash
python -m unittest discover -s tests -v
```
