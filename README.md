# DataDealer

A Flask web application that monitors a dedicated Gmail inbox for investment data requests from consultants, parses them with Claude AI, matches requests to uploaded files using semantic search, and either responds automatically or forwards to the appropriate CR team member with full context.

---

## How It Works

### The Pipeline

1. **Poll** — A background scheduler checks the Gmail inbox every N minutes for unread messages
2. **Region check** — If CR routing is configured and the sender's region is unknown, a clarification email is sent asking them to identify their region; the request is held until they reply
3. **Parse** — Claude AI extracts structured data from the email: firm, fund/strategy, vehicle, share class, data type, and time period
4. **Search** — The parsed request is converted to a text embedding and compared against all uploaded files using cosine similarity
5. **Permission check** — If the best-matching file is marked `restricted`, the sender must be on the approved list for that fund
6. **Route** — One of two outcomes:
   - **Auto-send**: sender is approved + file match score ≥ high-confidence threshold + Claude parse confidence is high → file is sent directly to the consultant
   - **Forward**: anything uncertain (unapproved sender, borderline match, low parse confidence, no file found) → forwarded to the assigned CR team member with full context, AI summary, best file match attached, and plain-English explanation of why it couldn't be auto-fulfilled
7. **Log** — Every request is recorded in the dashboard with full audit trail regardless of outcome

### CR Routing

When multiple CR team members cover different geographic regions, DataDealer can route forwarded requests to the right person automatically:

- Regions and team member assignments are configured in the Config page
- The first time a sender emails in, their region is unknown — DataDealer sends a one-time clarification email asking which region they're in
- Once they reply, their region is saved permanently and they're never asked again
- When routing to a region, DataDealer picks the team member with the **fewest currently outstanding requests** (load balancing), not just the first alphabetically

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, Flask, Flask-APScheduler |
| AI parsing | Anthropic Claude API (tool-use structured extraction) |
| Semantic search | `sentence-transformers` (`all-MiniLM-L6-v2`), `numpy` — runs locally, no API cost |
| Email | Gmail API with OAuth 2.0 |
| Database | SQLite (raw `sqlite3`, no ORM) |
| Frontend | Jinja2 templates, Bootstrap 5.3, Bootstrap Icons, Inter font |

---

## Features

- **Automatic responses** — High-confidence matches sent directly to consultants without human intervention
- **Smart forwarding** — Uncertain requests forwarded to the right CR team member with AI summary, best file match attached, and explanation of why it couldn't be auto-fulfilled
- **Load-balanced CR routing** — Requests routed to the CR member with the fewest outstanding requests in the sender's region
- **Permission system** — Per-fund, per-vehicle, per-share-class approval lists with ownership tracking (only the person who granted a permission can revoke it)
- **Semantic file search** — Files matched by meaning, not just filename keywords
- **Stale file detection** — Dashboard flags files overdue for an update based on configured cadence (monthly, quarterly, annual)
- **Strategy browser** — Hierarchical view of all strategies ever uploaded (firm → style → asset class → region → fund → vehicle), survives file deletion
- **File supersession** — Uploading a new version of a file marks the old one as superseded and excludes it from future searches
- **Full audit log** — Every request logged with sender, AI parse result, match score, outcome, and forwarding destination
- **Review queue** — Forwarded requests visible in the dashboard for human follow-up; admin can re-run matching after uploading new files
- **Public vs. restricted files** — Public files (e.g. mutual fund factsheets) bypass the permission check entirely
- **AI metadata suggestions** — When uploading a file, Claude analyzes the content and pre-fills the metadata form
- **Multi-user authentication** — Role-based login system with two roles: `admin` (full access) and `cr_member` (scoped to their assigned requests). Accounts managed via the Users page; initial admin seeded from env vars on first startup

---

## Setup

### Prerequisites

- Python 3.11+
- A Google Cloud project with the Gmail API enabled
- OAuth 2.0 credentials (`credentials.json`) downloaded from Google Cloud Console
- An Anthropic API key

### Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
```

Edit `.env` with your values — see the full variable reference below.

Place your `credentials.json` in the `credentials/` directory.

### First Run

```bash
python app.py
```

On first run, a browser window will open for Gmail OAuth consent. Complete the flow — this creates `credentials/token.json` and won't happen again unless the token is deleted or expires.

Visit `http://localhost:5001` for the dashboard.

### Manual Poll (for testing)

```bash
python poll_now.py
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in these values:

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Your Anthropic API key — get it from [console.anthropic.com](https://console.anthropic.com) |
| `GMAIL_INBOX_EMAIL` | Yes | The dedicated Gmail address DataDealer monitors for incoming requests |
| `CONSULTANT_EMAIL` | Yes | Fallback email for forwarded requests when CR routing is not configured |
| `ADMIN_EMAIL` | Yes | Email address for the initial admin account created on first startup |
| `ADMIN_PASSWORD` | Yes | Password for the initial admin account. After first run, manage all accounts via the Users page |
| `SECRET_KEY` | Yes | Random string for Flask session security — run `python -c "import secrets; print(secrets.token_hex(32))"` to generate one |
| `SIMILARITY_THRESHOLD` | No | Minimum cosine similarity to consider a file a candidate match (default: `0.65`) |
| `HIGH_CONFIDENCE_THRESHOLD` | No | Score above which a match triggers auto-send, assuming sender is approved and parse confidence is high (default: `0.82`) |
| `POLL_INTERVAL_MINUTES` | No | How often to check Gmail for new messages (default: `5`) |
| `GMAIL_CREDENTIALS_FILE` | No | Path to `credentials.json` (default: `credentials/credentials.json`) |
| `GMAIL_TOKEN_FILE` | No | Path to `token.json` (default: `credentials/token.json`) |
| `UPLOAD_FOLDER` | No | Where uploaded files are stored on disk (default: `uploads`) |
| `DATABASE_PATH` | No | Path to the SQLite database file (default: `database/datadealer.db`) |
| `NOTIFICATION_EMAIL` | No | If set, receives a brief email notification whenever a request enters `pending_clarification` status |

### Cloud Deployment (Railway etc.)

For platforms where credential files can't be committed to the repo, encode them as base64 environment variables:

```bash
base64 -i credentials/credentials.json  # → set as GMAIL_CREDENTIALS_JSON
base64 -i credentials/token.json        # → set as GMAIL_TOKEN_JSON
```

DataDealer will write these to the expected paths on startup if the files don't exist on disk.

---

## Dashboard Pages

| Page | Who can access | What it does |
|---|---|---|
| **Dashboard** | All logged-in users | Admins see system-wide stats (total requests, auto-sent, forwarded, files indexed, stale files). CR members see their own queue stats (assigned, needs attention, resolved) |
| **Strategies** | All logged-in users | Browse all strategies ever uploaded in a collapsible hierarchy; manage permissions per fund |
| **Review Queue** | All logged-in users | Admins see all forwarded/flagged requests and can filter by assignee. CR members see only their own assigned requests |
| **Request Log** | All logged-in users | Admins see full audit trail. CR members see only their assigned requests |
| **Upload Files** | Admin only | Upload new files, set metadata (firm, fund, vehicle, data type, access level), supersede old versions |
| **Permissions** | Admin only | Approve specific consultants to receive auto-responses for specific funds/vehicles/share classes |
| **Config** | Admin only | Configure CR regions, assign team members, view known sender profiles and current load counts |
| **Users** | Admin only | Create accounts, reset passwords, deactivate/reactivate users |
| **Change Password** | All logged-in users | Self-service password change |

---

## Database Schema

| Table | Purpose |
|---|---|
| `files` | Uploaded file metadata + embedding vector |
| `permissions` | Approved sender → fund/vehicle/share class mappings with ownership tracking |
| `requests` | Full audit log of every email processed |
| `strategies` | Permanent record of every strategy ever uploaded (survives file deletion) |
| `cr_regions` | Named geographic regions for CR routing |
| `cr_assignments` | CR team members assigned to each region |
| `sender_profiles` | Known sender → region mappings (saved after region clarification) |
| `users` | Dashboard user accounts (email, hashed password, role, active status) |

---

## File Support

Uploaded files can be: `.pdf`, `.xlsx`, `.xls`, `.csv`, `.docx`, `.pptx`

Files are stored on disk in `UPLOAD_FOLDER` and indexed in the database with a semantic embedding. The embedding is generated locally using `sentence-transformers` — no API call required.

---

## Safety Notes

- **Nothing is ever sent without a file being attached to a request.** Auto-sends require: approved sender + high similarity score + high parse confidence. All three must be true.
- **Forwarded emails include the matched file as an attachment** so the CR team member can respond manually without needing to find the file themselves.
- **Restricted files require explicit permission.** Files marked `restricted` (the default) will never be auto-sent to an unapproved sender — the request is always forwarded instead.
- **Public files skip the permission check.** Files marked `public` (e.g. mutual fund factsheets) are sent to any sender who requests them.
- **Permission ownership.** Each permission records who granted it. Only that person can revoke it, preventing accidental removal.
