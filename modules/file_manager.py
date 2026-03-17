"""
file_manager.py — File upload, storage, and semantic search
─────────────────────────────────────────────────────────────
Handles:
  1. Saving uploaded files to disk in an organized folder structure
  2. Generating text embeddings for each file's metadata (used for search)
  3. Searching for the best-matching file given a text query

Semantic search means we can match "Q3 performance for the flagship strategy"
to a file tagged as "Flagship Fund quarterly returns 2024 Q3" — no exact
keyword match required.
"""

import os
import json
import datetime
import calendar
import numpy as np
from werkzeug.utils import secure_filename

import config
from modules.database import get_db


# ── Embedding Model ───────────────────────────────────────────────────────────
# We load the model lazily (only when first needed) to avoid slowing down startup.
# all-MiniLM-L6-v2 is a compact model (~80MB) that works very well for
# semantic similarity of short text descriptions.
_embedding_model = None


def _get_model():
    """Load and cache the sentence-transformers embedding model."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        print("[Files] Loading embedding model (first time only — may take 15-20 seconds)...")
        # all-mpnet-base-v2 is significantly more accurate than all-MiniLM-L6-v2 at
        # distinguishing fine-grained differences (e.g. large cap vs small cap growth).
        # It's larger (~400MB) but only loads once per session.
        _embedding_model = SentenceTransformer("all-mpnet-base-v2")
        print("[Files] Embedding model loaded.")
    return _embedding_model


def generate_embedding(text: str) -> list[float]:
    """
    Convert a text string into a list of floats (a vector embedding).
    Similar texts will produce similar vectors — this is how semantic search works.
    """
    model = _get_model()
    embedding = model.encode(text)
    return embedding.tolist()  # Convert numpy array → Python list for JSON storage


def cosine_similarity(vec_a: list, vec_b: list) -> float:
    """
    Compute the cosine similarity between two embedding vectors.
    Returns a value between 0.0 (completely different) and 1.0 (identical).
    """
    a = np.array(vec_a)
    b = np.array(vec_b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def build_file_text(firm_name, asset_class, region, fund_name, vehicle, share_class,
                    investment_style, data_type, time_period, description) -> str:
    """
    Build a single descriptive text string from a file's full taxonomy metadata.
    Full hierarchy: Firm → Asset Class → Region → Strategy → Vehicle → Share Class
    Investment style (Active/Passive/Smart Beta) is included so queries mentioning
    'index fund' or 'passive' correctly favour passive-tagged files over active ones.
    """
    parts = []
    if firm_name:
        parts.append(firm_name)
    if asset_class:
        parts.append(asset_class)
    if region:
        parts.append(region)
    if fund_name:
        parts.append(fund_name)
    # Add the combined "Firm Fund" phrase explicitly so that queries like
    # "Vanguard Growth ETF" match a file tagged firm="Vanguard", fund="Growth ETF".
    if firm_name and fund_name:
        parts.append(f"{firm_name} {fund_name}")
    if vehicle:
        parts.append(vehicle)
    if share_class:
        parts.append(share_class)
    if investment_style and investment_style != "Not Applicable":
        parts.append(investment_style)
    if data_type:
        parts.append(data_type.replace("_", " "))
    if time_period:
        parts.append(time_period)
    if description:
        parts.append(description)
    return " ".join(parts)


# ── File Upload ───────────────────────────────────────────────────────────────

def _calculate_next_update_date(upload_date_str: str, update_cadence: str):
    """Calculate the expected next update date based on upload date and cadence."""
    if not update_cadence or update_cadence == "ad-hoc":
        return None
    try:
        upload_date = datetime.datetime.fromisoformat(upload_date_str).date()
        if update_cadence == "monthly":
            month = upload_date.month + 1
            year = upload_date.year
            if month > 12:
                month, year = 1, year + 1
            day = min(upload_date.day, calendar.monthrange(year, month)[1])
            return datetime.date(year, month, day).isoformat()
        elif update_cadence == "quarterly":
            month = upload_date.month + 3
            year = upload_date.year
            while month > 12:
                month -= 12
                year += 1
            day = min(upload_date.day, calendar.monthrange(year, month)[1])
            return datetime.date(year, month, day).isoformat()
        elif update_cadence == "annually":
            try:
                return datetime.date(upload_date.year + 1, upload_date.month, upload_date.day).isoformat()
            except ValueError:
                return datetime.date(upload_date.year + 1, upload_date.month, 28).isoformat()
    except Exception:
        return None


def is_stale(file: dict) -> bool:
    """Return True if the file is past its expected update date + grace period."""
    next_update = file.get("next_update_date")
    if not next_update:
        return False
    try:
        import datetime as dt
        grace = dt.timedelta(days=config.FRESHNESS_GRACE_PERIOD_DAYS)
        return dt.date.today() > dt.date.fromisoformat(next_update) + grace
    except Exception:
        return False


def get_stale_files() -> list:
    """Return all files that are past their expected update date + grace period."""
    import datetime as dt
    grace = dt.timedelta(days=config.FRESHNESS_GRACE_PERIOD_DAYS)
    cutoff = (dt.date.today() - grace).isoformat()
    conn = get_db()
    rows = conn.execute(
        """SELECT id, filename, firm_name, asset_class, region, fund_name,
                  update_cadence, next_update_date, upload_date
           FROM files
           WHERE update_cadence IS NOT NULL
             AND update_cadence != ''
             AND update_cadence != 'ad-hoc'
             AND next_update_date IS NOT NULL
             AND next_update_date <= ?
             AND superseded_by IS NULL
           ORDER BY next_update_date ASC""",
        (cutoff,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def allowed_file(filename: str) -> bool:
    """Check that a file has an allowed extension."""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in config.ALLOWED_EXTENSIONS
    )


def save_file(file, firm_name: str, asset_class: str, region: str, fund_name: str,
              vehicle: str, share_class: str, investment_style: str, data_type: str,
              time_period: str, access_level: str, description: str,
              update_cadence: str = "", supersede_file_id: int = None) -> dict | None:
    """
    Save an uploaded file to disk and store its metadata + embedding in the database.

    Files are organized on disk following the full taxonomy:
        uploads / Asset Class / Region / Strategy / filename

    Args:
        file:         The file object from Flask's request.files
        asset_class:  e.g. "Equity", "Fixed Income"
        region:       e.g. "US", "Global", "Emerging Markets"
        fund_name:    The strategy name, e.g. "Large Cap Growth"
        vehicle:      Legal/structural wrapper, e.g. "Mutual Fund", "LP", "CIT", "ETF"
        share_class:  Share class within the vehicle, e.g. "Class I", "Class A"
        data_type:    e.g. "monthly_returns"
        time_period:  e.g. "Q3 2024"
        description:  Free-text description to improve search accuracy
    """
    if not file or file.filename == "":
        return None

    if not allowed_file(file.filename):
        raise ValueError(f"File type not allowed. Allowed types: {config.ALLOWED_EXTENSIONS}")

    filename = secure_filename(file.filename)

    def safe_name(s):
        return "".join(c for c in s if c.isalnum() or c in (" ", "-", "_")).strip()

    # Organize files on disk: uploads/Firm/AssetClass/Region/Strategy/
    folder = os.path.join(
        config.UPLOAD_FOLDER,
        safe_name(firm_name),
        safe_name(asset_class),
        safe_name(region),
        safe_name(fund_name),
    )
    os.makedirs(folder, exist_ok=True)

    file_path = os.path.join(folder, filename)
    if os.path.exists(file_path):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name, ext = os.path.splitext(filename)
        filename = f"{name}_{timestamp}{ext}"
        file_path = os.path.join(folder, filename)

    file.save(file_path)
    print(f"[Files] Saved file to: {file_path}")

    file_text = build_file_text(firm_name, asset_class, region, fund_name, vehicle, share_class, investment_style, data_type, time_period, description)
    embedding = generate_embedding(file_text)

    upload_date_str = datetime.datetime.now().isoformat()
    next_update_date = _calculate_next_update_date(upload_date_str, update_cadence)

    conn = get_db()
    cursor = conn.execute(
        """
        INSERT INTO files (filename, file_path, firm_name, asset_class, region, fund_name,
                           vehicle, share_class, investment_style, data_type, time_period,
                           access_level, upload_date, description, embedding,
                           update_cadence, next_update_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            filename,
            file_path,
            firm_name,
            asset_class,
            region,
            fund_name,
            vehicle or "",
            share_class or "",
            investment_style or "Not Applicable",
            data_type,
            time_period or "",
            access_level or "restricted",
            upload_date_str,
            description or "",
            json.dumps(embedding),
            update_cadence or "",
            next_update_date,
        ),
    )
    # Register the strategy — INSERT OR IGNORE keeps a permanent record even if the
    # file is later deleted. This is what populates the Strategy Browser.
    conn.execute(
        """
        INSERT OR IGNORE INTO strategies
            (firm_name, investment_style, asset_class, region, fund_name, vehicle, share_class, created_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            firm_name,
            investment_style or "Not Applicable",
            asset_class,
            region,
            fund_name,
            vehicle or "",
            share_class or "",
            datetime.datetime.now().isoformat(),
        ),
    )

    conn.commit()
    file_id = cursor.lastrowid

    if supersede_file_id:
        conn.execute(
            "UPDATE files SET superseded_by = ? WHERE id = ?",
            (file_id, supersede_file_id)
        )
        conn.commit()
        print(f"[Files] File {supersede_file_id} superseded by new file {file_id}.")

    conn.close()

    print(f"[Files] Saved file metadata to database. File ID: {file_id}")
    return {"id": file_id, "filename": filename, "file_path": file_path}


# ── Semantic Search ───────────────────────────────────────────────────────────

def search_files(query_text: str) -> tuple[dict | None, float]:
    """
    Find the best-matching file for a given text query using semantic similarity.

    How it works:
      1. Convert the query to an embedding vector
      2. Load all file embeddings from the database
      3. Compute cosine similarity between the query and each file
      4. Return the best match if its score exceeds the configured threshold

    Args:
        query_text: A plain-English description of what's being requested
                    (e.g. "Flagship Fund LP monthly returns Q3 2024")

    Returns:
        A tuple of (matched_file_row, similarity_score).
        matched_file_row is None if no file scored above the threshold.
    """
    query_embedding = generate_embedding(query_text)

    conn = get_db()
    files = conn.execute("SELECT * FROM files WHERE superseded_by IS NULL").fetchall()
    conn.close()

    if not files:
        print("[Files] No files in database to search.")
        return None, 0.0

    best_match = None
    best_score = -1.0

    for file in files:
        if not file["embedding"]:
            continue  # Skip files without embeddings (shouldn't happen normally)
        file_embedding = json.loads(file["embedding"])
        score = cosine_similarity(query_embedding, file_embedding)
        print(f"[Files] '{file['filename']}' similarity score: {score:.3f}")
        if score > best_score:
            best_score = score
            best_match = file

    if best_score >= config.SIMILARITY_THRESHOLD:
        print(f"[Files] Best match: '{best_match['filename']}' (score: {best_score:.3f})")
        return dict(best_match), best_score
    else:
        print(f"[Files] No match above threshold {config.SIMILARITY_THRESHOLD}. "
              f"Best score was: {best_score:.3f}")
        return None, best_score


def get_all_files() -> list:
    """Return all files in the database (for the admin dashboard)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT f.id, f.filename, f.firm_name, f.asset_class, f.region, f.fund_name, "
        "f.vehicle, f.share_class, f.investment_style, f.data_type, f.time_period, "
        "f.access_level, f.upload_date, f.description, f.update_cadence, f.next_update_date, "
        "f.superseded_by, nf.filename as superseded_by_filename "
        "FROM files f "
        "LEFT JOIN files nf ON nf.id = f.superseded_by "
        "ORDER BY f.firm_name, f.asset_class, f.region, f.fund_name, f.upload_date DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_file(file_id: int) -> bool:
    """
    Remove a file from the database and delete it from disk.
    Returns True if successful, False if the file wasn't found.
    """
    conn = get_db()
    row = conn.execute("SELECT file_path FROM files WHERE id = ?", (file_id,)).fetchone()
    if not row:
        conn.close()
        return False

    file_path = row["file_path"]

    # Delete from database first
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    conn.commit()
    conn.close()

    # Delete from disk (only if the file actually exists)
    if os.path.exists(file_path):
        os.remove(file_path)
        print(f"[Files] Deleted file from disk: {file_path}")

    return True
