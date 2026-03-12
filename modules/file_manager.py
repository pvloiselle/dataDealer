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
        print("[Files] Loading embedding model (first time only — may take 30 seconds)...")
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
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


def build_file_text(fund_name, vehicle_name, data_type, time_period, description) -> str:
    """
    Build a single descriptive text string from a file's metadata.
    This is what gets embedded and compared during search.

    More descriptive = better matching. We combine all tags into one sentence.
    """
    parts = []
    if fund_name:
        parts.append(fund_name)
    if vehicle_name:
        parts.append(vehicle_name)
    if data_type:
        parts.append(data_type.replace("_", " "))
    if time_period:
        parts.append(time_period)
    if description:
        parts.append(description)
    return " ".join(parts)


# ── File Upload ───────────────────────────────────────────────────────────────

def allowed_file(filename: str) -> bool:
    """Check that a file has an allowed extension."""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in config.ALLOWED_EXTENSIONS
    )


def save_file(file, fund_name: str, vehicle_name: str, data_type: str,
              time_period: str, description: str) -> dict | None:
    """
    Save an uploaded file to disk and store its metadata + embedding in the database.

    Args:
        file:         The file object from Flask's request.files
        fund_name:    e.g. "Flagship Fund"
        vehicle_name: e.g. "LP" or "Offshore"
        data_type:    e.g. "monthly_returns"
        time_period:  e.g. "Q3 2024"
        description:  Free-text description to improve search accuracy

    Returns:
        A dict with the saved file's database record, or None on failure.
    """
    if not file or file.filename == "":
        return None

    if not allowed_file(file.filename):
        raise ValueError(f"File type not allowed. Allowed types: {config.ALLOWED_EXTENSIONS}")

    # Sanitize the filename to prevent directory traversal attacks
    filename = secure_filename(file.filename)

    # Organize files in subfolders by fund name
    # Removes characters that are invalid in folder names
    safe_fund = "".join(c for c in fund_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
    fund_folder = os.path.join(config.UPLOAD_FOLDER, safe_fund)
    os.makedirs(fund_folder, exist_ok=True)

    # If a file with this name already exists, add a timestamp to avoid overwriting
    file_path = os.path.join(fund_folder, filename)
    if os.path.exists(file_path):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name, ext = os.path.splitext(filename)
        filename = f"{name}_{timestamp}{ext}"
        file_path = os.path.join(fund_folder, filename)

    # Save the file to disk
    file.save(file_path)
    print(f"[Files] Saved file to: {file_path}")

    # Generate a semantic embedding from the file's metadata
    file_text = build_file_text(fund_name, vehicle_name, data_type, time_period, description)
    embedding = generate_embedding(file_text)

    # Store the metadata and embedding in the database
    conn = get_db()
    cursor = conn.execute(
        """
        INSERT INTO files (filename, file_path, fund_name, vehicle_name, data_type,
                           time_period, upload_date, description, embedding)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            filename,
            file_path,
            fund_name,
            vehicle_name or "",
            data_type,
            time_period or "",
            datetime.datetime.now().isoformat(),
            description or "",
            json.dumps(embedding),  # Store embedding as JSON text
        ),
    )
    conn.commit()
    file_id = cursor.lastrowid
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
    files = conn.execute("SELECT * FROM files").fetchall()
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


def get_all_files() -> list[dict]:
    """Return all files in the database (for the admin dashboard)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, filename, fund_name, vehicle_name, data_type, time_period, upload_date, description "
        "FROM files ORDER BY upload_date DESC"
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
