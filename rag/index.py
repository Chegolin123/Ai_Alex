"""RAG over the system's own configuration and logs.

SQLite FTS5 rather than a vector store: what needs retrieving here is the
system's own text - configs, skill prompts, error tails - and a lexical index
over a few dozen files answers that better than embeddings on 8 GB of VRAM that
the model already needs.
"""

import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runtime import state

ROOT = state.ROOT
DB_PATH = os.path.join(state.STATE_DIR, "rag.db")

INDEXABLE_DIRS = ["skills", "runtime", "agent", "bin", "state"]
INDEXABLE_SUFFIXES = (".py", ".md", ".ps1", ".cmd", ".json", ".yaml", ".yml", ".txt", ".log")
SKIP_DIR_PARTS = {"backups", "rag.db", "__pycache__", "node_modules", ".git"}

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
    path UNINDEXED,
    chunk_id UNINDEXED,
    body,
    tokenize = 'unicode61'
);
CREATE TABLE IF NOT EXISTS index_meta (
    path TEXT PRIMARY KEY,
    mtime REAL,
    size INTEGER,
    indexed_at TEXT
);
"""


def connect():
    os.makedirs(state.STATE_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def chunk(text, size=900, overlap=120):
    text = text.replace("\r\n", "\n")
    if len(text) <= size:
        return [text]
    out, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "))
            if cut > size * 0.5:
                end = start + cut + 1
        out.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def _walk():
    for d in INDEXABLE_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIR_PARTS]
            for fn in filenames:
                if fn.endswith(INDEXABLE_SUFFIXES):
                    yield os.path.join(dirpath, fn)


def index_file(con, path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return 0
    if not text.strip():
        return 0
    rel = os.path.relpath(path, ROOT)
    st = os.stat(path)
    row = con.execute("SELECT mtime, size FROM index_meta WHERE path = ?", (rel,)).fetchone()
    if row and row["mtime"] == st.st_mtime and row["size"] == st.st_size:
        return 0
    con.execute("DELETE FROM docs WHERE path = ?", (rel,))
    chunks = chunk(text)
    for i, c in enumerate(chunks):
        con.execute(
            "INSERT INTO docs (path, chunk_id, body) VALUES (?, ?, ?)",
            (rel, str(i), c),
        )
    con.execute(
        "INSERT OR REPLACE INTO index_meta (path, mtime, size, indexed_at) VALUES (?, ?, ?, ?)",
        (rel, st.st_mtime, st.st_size, state.now()),
    )
    return len(chunks)


def index_paths(paths=None, verbose=False):
    con = connect()
    added = 0
    files = 0
    targets = paths if paths is not None else list(_walk())
    for p in targets:
        n = index_file(con, p)
        if n:
            files += 1
            added += n
            if verbose:
                print(f"  indexed {os.path.relpath(p, ROOT)} ({n} chunks)")
    con.commit()
    total = con.execute("SELECT count(*) c FROM docs").fetchone()["c"]
    con.close()
    state.log_event("rag_index", files_changed=files, chunks_added=added, chunks_total=total)
    return {"files_changed": files, "chunks_added": added, "chunks_total": total}


def search(query, limit=5):
    con = connect()
    try:
        rows = con.execute(
            "SELECT path, chunk_id, snippet(docs, 2, '[', ']', ' ... ', 24) AS snip, "
            "       bm25(docs) AS score "
            "FROM docs WHERE docs MATCH ? ORDER BY score LIMIT ?",
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        cleaned = re.sub(r"[^\w\s]", " ", query)
        rows = con.execute(
            "SELECT path, chunk_id, snippet(docs, 2, '[', ']', ' ... ', 24) AS snip, "
            "       bm25(docs) AS score "
            "FROM docs WHERE docs MATCH ? ORDER BY score LIMIT ?",
            (cleaned, limit),
        ).fetchall() if cleaned.strip() else []
    finally:
        con.close()
    return [{"path": r["path"], "chunk": r["chunk_id"], "snippet": r["snip"], "score": round(r["score"], 3)}
            for r in rows]


def stats():
    con = connect()
    try:
        return {
            "chunks": con.execute("SELECT count(*) c FROM docs").fetchone()["c"],
            "files": con.execute("SELECT count(*) c FROM index_meta").fetchone()["c"],
            "db_bytes": os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
        }
    finally:
        con.close()


if __name__ == "__main__":
    import json
    t0 = time.time()
    print(json.dumps(index_paths(verbose=True), indent=2))
    print(json.dumps(stats(), indent=2))
    print(f"{time.time() - t0:.2f}s")
