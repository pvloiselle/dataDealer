# dataDealer

A Flask web application that monitors a Gmail inbox for data requests from investment consultants, parses them with Claude AI, matches requests to relevant files using semantic search, and auto-drafts email replies for human review and sending.

## What It Does

1. A background scheduler polls a Gmail inbox every few minutes for unread emails
2. Claude AI extracts structured data from each email — fund name, vehicle/share class, data type, and time period
3. The system checks whether the sender is approved to receive the requested fund data
4. If approved, it generates a text embedding of the request and searches the file database by semantic similarity
5. When a match is found above the confidence threshold, it creates a Gmail draft reply with the matching file attached
6. A human reviews the draft in Gmail and sends manually
7. All requests are logged to the dashboard for audit and review

## Tech Stack

- **Backend:** Python, Flask, Flask-APScheduler
- **AI:** Anthropic Claude API (structured email parsing via tool use)
- **Semantic Search:** sentence-transformers, numpy
- **Email:** Gmail API with OAuth2
- **Database:** SQLite
- **Frontend:** Jinja2 templates

## Setup

### Prerequisites

- Python 3.11+
- A Google Cloud project with Gmail API enabled
- OAuth2 credentials (`credentials.json`) downloaded from Google Cloud Console
- Anthropic API key

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

Edit `.env` with your values:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `GMAIL_ADDRESS` | The inbox to monitor |
| `SIMILARITY_THRESHOLD` | Minimum similarity score to auto-match (0.0–1.0, default 0.7) |
| `POLL_INTERVAL_MINUTES` | How often to poll Gmail (default 5) |

Place your `credentials.json` and `token.json` in the `credentials/` directory.

### Run

```bash
python app.py
```

Visit `http://localhost:5000` for the dashboard.

To trigger a manual poll:

```bash
python poll_now.py
```

## Features

- Gmail OAuth2 integration with configurable scopes
- Claude tool-use for structured extraction (fund name, vehicle, data type, date range)
- Sender permission management via the dashboard
- Semantic file matching using sentence embeddings
- Configurable similarity threshold
- Audit log of all requests with match status
- File upload interface supporting PDF, XLSX, XLS, CSV, DOCX, PPTX
- Draft-only replies — nothing is sent without human approval
