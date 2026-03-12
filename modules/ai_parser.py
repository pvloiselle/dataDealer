"""
ai_parser.py — Email parsing with Claude AI
─────────────────────────────────────────────
Uses the Anthropic Claude API with "tool use" to reliably extract structured
data from an investment consultant's email request.

Why tool use instead of plain text prompting?
  When you ask Claude to "return JSON", it usually works but can occasionally
  produce extra text or malformed output. Tool use forces Claude to return
  a structured object that matches a strict schema — no parsing guesswork needed.
"""

from anthropic import Anthropic
import config


def parse_email_request(subject: str, body: str) -> dict | None:
    """
    Send an email's subject + body to Claude and extract structured request data.

    Returns a dict with these keys:
        fund_name       (str or None)   — e.g. "Flagship Fund"
        vehicle_name    (str or None)   — e.g. "LP", "Offshore", "Class A"
        data_type       (str)           — e.g. "monthly_returns", "fee_schedule"
        time_period     (str or None)   — e.g. "Q3 2024", "YTD 2024"
        confidence      (str)           — "high", "medium", or "low"
        summary         (str)           — human-readable summary of the request

    Returns None if parsing fails completely.
    """

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # Define the tool schema — Claude MUST call this tool, which forces it
    # to structure its response according to our schema.
    tools = [
        {
            "name": "extract_data_request",
            "description": (
                "Extract the structured data request from an investment consultant's email. "
                "Identify what fund, vehicle, data type, and time period they are asking for."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "fund_name": {
                        "type": "string",
                        "description": (
                            "The name of the investment fund being requested. "
                            "Return null if not specified or unclear."
                        ),
                    },
                    "vehicle_name": {
                        "type": "string",
                        "description": (
                            "The specific vehicle, share class, or structure (e.g. 'LP', "
                            "'Offshore Fund', 'Class A', 'UCITS'). Return null if not specified."
                        ),
                    },
                    "data_type": {
                        "type": "string",
                        "enum": [
                            "monthly_returns",
                            "quarterly_returns",
                            "annual_returns",
                            "fee_schedule",
                            "factsheet",
                            "aum_data",
                            "attribution_report",
                            "risk_report",
                            "portfolio_holdings",
                            "other",
                        ],
                        "description": (
                            "The type of data being requested. Choose the closest match. "
                            "Use 'other' only if it genuinely doesn't fit any category."
                        ),
                    },
                    "time_period": {
                        "type": "string",
                        "description": (
                            "The time period the consultant is asking about "
                            "(e.g. 'Q3 2024', 'YTD 2024', 'last 3 years', 'December 2023'). "
                            "Return null if not specified."
                        ),
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": (
                            "How confident you are in your extraction. "
                            "Use 'high' if everything is clear, 'medium' if some things are "
                            "inferred, 'low' if the request is vague or ambiguous."
                        ),
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "A brief, plain-English summary of what the consultant is requesting. "
                            "1-2 sentences. This will be shown to a human reviewer. "
                            "Example: 'Monthly returns for the Flagship LP fund for Q3 2024.'"
                        ),
                    },
                },
                "required": ["data_type", "confidence", "summary"],
            },
        }
    ]

    # Compose the prompt with the email content
    prompt = (
        "You are processing an email from an investment consultant who is requesting "
        "fund data from an investment management firm. Analyze the email carefully and "
        "extract the key details about what they are requesting.\n\n"
        f"Email Subject: {subject}\n\n"
        f"Email Body:\n{body}\n\n"
        "Extract the request details using the provided tool."
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            tools=tools,
            # "any" forces Claude to use a tool — it cannot respond with plain text
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": prompt}],
        )

        # Find the tool_use block in the response and return its input
        for block in response.content:
            if block.type == "tool_use":
                parsed = block.input
                print(f"[AI Parser] Extracted request: {parsed.get('summary', 'no summary')}")
                print(f"[AI Parser] Confidence: {parsed.get('confidence', 'unknown')}")
                return parsed

        print("[AI Parser] Warning: Claude did not call the extraction tool.")
        return None

    except Exception as e:
        print(f"[AI Parser] Error calling Claude API: {e}")
        return None


def build_search_query(parsed: dict) -> str:
    """
    Build a plain-text search string from parsed request data.
    This string is used to generate an embedding for semantic file matching.

    Example output:
        "Flagship Fund LP monthly returns Q3 2024"
    """
    parts = []
    if parsed.get("fund_name"):
        parts.append(parsed["fund_name"])
    if parsed.get("vehicle_name"):
        parts.append(parsed["vehicle_name"])
    if parsed.get("data_type"):
        # Convert underscore format to readable words for better embedding
        parts.append(parsed["data_type"].replace("_", " "))
    if parsed.get("time_period"):
        parts.append(parsed["time_period"])

    query = " ".join(parts)
    print(f"[AI Parser] Search query for embedding: '{query}'")
    return query
