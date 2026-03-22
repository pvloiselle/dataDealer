"""
ai_analyzer.py — AI-assisted file metadata suggestion
───────────────────────────────────────────────────────
When a file is selected for upload, this module reads its content and asks
Claude to suggest appropriate metadata tags (firm, fund, asset class, etc.).

The suggestions pre-fill the upload form — a human must review and confirm
before anything is saved. Access level (Public/Restricted) is intentionally
excluded from AI suggestions and always left to the human.
"""

import io
import datetime
from anthropic import Anthropic
import config


def extract_pdf_text(file_bytes: bytes, max_chars: int = 4000) -> str:
    """
    Extract plain text from a PDF's first few pages.
    We only need enough to identify the fund — full extraction isn't necessary.
    """
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            text = ""
            for page in pdf.pages[:4]:  # first 4 pages is plenty
                page_text = page.extract_text() or ""
                text += page_text + "\n"
                if len(text) >= max_chars:
                    break
            return text[:max_chars]
    except Exception as e:
        print(f"[Analyzer] PDF text extraction failed: {e}")
        return ""


def analyze_file_for_metadata(filename: str, file_bytes: bytes) -> dict:
    """
    Send file content to Claude and return suggested metadata fields.

    ⚠️  access_level is intentionally NOT included in suggestions.
        That decision must always be made by a human.

    Args:
        filename:   Original filename (e.g. "Vanguard Growth ETF Factsheet Q4 2025.pdf")
        file_bytes: Raw file content as bytes

    Returns:
        Dict of suggested field values. Empty dict if analysis fails.
    """
    # Extract text for PDFs; for other types use filename only
    doc_text = ""
    if filename.lower().endswith(".pdf"):
        doc_text = extract_pdf_text(file_bytes)

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    today = datetime.date.today()

    # Build the content block for Claude
    content = f"Filename: {filename}\n\n"
    if doc_text:
        content += f"Document text (first ~4000 characters):\n{doc_text}"
    else:
        content += (
            "(Non-PDF file — the filename is the primary source of information. "
            "Infer as much as possible from it.)"
        )

    tools = [
        {
            "name": "suggest_metadata",
            "description": (
                "Suggest metadata tags for an investment fund document being uploaded "
                "to a data management platform. Be as specific and accurate as possible."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "firm_name": {
                        "type": "string",
                        "description": (
                            "The investment management firm (parent company) — "
                            "e.g. 'Vanguard', 'BlackRock', 'Fidelity'. "
                            "Return null if unclear."
                        ),
                    },
                    "investment_style": {
                        "type": "string",
                        "enum": ["Active", "Passive", "Smart Beta / Factor", "Not Applicable"],
                        "description": (
                            "Active (stock picking / discretionary management), "
                            "Passive (index tracking), Smart Beta / Factor (rules-based), "
                            "or Not Applicable (for alternatives, real assets, cash, etc.)."
                        ),
                    },
                    "asset_class": {
                        "type": "string",
                        "enum": [
                            "Equity", "Fixed Income", "Multi-Asset",
                            "Alternatives", "Real Assets", "Cash & Equivalents", "Other"
                        ],
                        "description": "The broad asset class of this fund.",
                    },
                    "region": {
                        "type": "string",
                        "enum": ["US", "International", "Global", "Emerging Markets", "Other"],
                        "description": "The geographic investment mandate.",
                    },
                    "fund_name": {
                        "type": "string",
                        "description": (
                            "The specific strategy or fund name — "
                            "e.g. 'Large Cap Growth', 'Total Bond Market', 'S&P 500 Index'. "
                            "Return null if unclear."
                        ),
                    },
                    "vehicle": {
                        "type": "string",
                        "enum": [
                            "Mutual Fund", "ETF", "CIT", "LP", "GP",
                            "UCITS", "Offshore Fund", "Separately Managed Account", "Other", ""
                        ],
                        "description": "The legal/structural wrapper for this fund.",
                    },
                    "share_class": {
                        "type": "string",
                        "enum": [
                            "Class A", "Class B", "Class C", "Class I",
                            "Class R", "Class Z", "Institutional", "Retail", "Other", ""
                        ],
                        "description": "The share class, if identifiable.",
                    },
                    "data_type": {
                        "type": "string",
                        "enum": [
                            "monthly_returns", "quarterly_returns", "annual_returns",
                            "fee_schedule", "factsheet", "aum_data", "attribution_report",
                            "risk_report", "portfolio_holdings", "other",
                        ],
                        "description": "What kind of data this document contains.",
                    },
                    "time_period": {
                        "type": "string",
                        "description": (
                            f"The time period this document covers. Today is {today.strftime('%B %d, %Y')}. "
                            "Always include a year — e.g. 'Q4 2025', 'December 2025', 'Full Year 2024'. "
                            "For the most recently completed quarter relative to today, infer accordingly. "
                            "Return null if not applicable (e.g. fee schedules)."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "A clear, specific description of this document for semantic search. "
                            "Include distinguishing details like market cap (large/small/mid), "
                            "benchmark name, investment objective, and any other specifics "
                            "that would help differentiate it from similar funds. "
                            "2-4 sentences."
                        ),
                    },
                },
                "required": ["asset_class", "data_type", "description"],
            },
        }
    ]

    prompt = (
        f"Today is {today.strftime('%B %d, %Y')}. "
        "Analyze this investment fund document and suggest accurate metadata tags for our data platform. "
        "Pay particular attention to details that distinguish this fund from similar ones "
        "(e.g. large cap vs small cap, active vs passive, specific benchmark). "
        "For the description, be as specific as possible — it is used for semantic search matching.\n\n"
        f"{content}"
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            tools=tools,
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": prompt}],
        )

        for block in response.content:
            if block.type == "tool_use":
                suggestions = block.input
                print(f"[Analyzer] Suggestions generated for: {filename}")
                print(f"[Analyzer] Suggested fund: {suggestions.get('fund_name', '—')}, "
                      f"type: {suggestions.get('data_type', '—')}")
                return suggestions

        return {}

    except Exception as e:
        print(f"[Analyzer] Error analyzing file: {e}")
        return {}
