import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - optional dependency
    SentenceTransformer = None


@dataclass
class LiteratureDocument:
    doc_id: str
    title: str
    source_path: str
    text: str
    metadata: dict = field(default_factory=dict)


@dataclass
class LiteratureChunk:
    chunk_uid: str
    doc_id: str
    title: str
    source_path: str
    heading: str
    text: str
    vector: np.ndarray
    score: float = 0.0


class LiteratureRAG:
    _EMBEDDER_CACHE = {}

    def __init__(
        self,
        documents,
        db_path,
        chunk_chars=1800,
        overlap_chars=240,
        vector_dim=768,
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    ):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.chunk_chars = max(400, int(chunk_chars))
        self.overlap_chars = max(0, int(overlap_chars))
        self.vector_dim = max(128, int(vector_dim))
        self.embedding_model = str(embedding_model or "").strip() or "sentence-transformers/all-MiniLM-L6-v2"
        self.documents = self._normalize_documents(documents)
        self.idf_lookup = {}
        self.chunks = []
        self._embedder = self._load_embedder()
        if self._embedder is not None:
            try:
                self.vector_dim = int(self._embedder.get_sentence_embedding_dimension() or self.vector_dim)
            except Exception:
                pass
        self.encoder_backend = "sentence_transformers" if self._embedder is not None else "hashed_tfidf"
        self._load_or_rebuild_store()

    @classmethod
    def _load_embedder(cls, model_name=None):
        if SentenceTransformer is None:
            return None
        key = str(model_name or "").strip() or "sentence-transformers/all-MiniLM-L6-v2"
        if key in cls._EMBEDDER_CACHE:
            return cls._EMBEDDER_CACHE[key]
        try:
            cls._EMBEDDER_CACHE[key] = SentenceTransformer(key)
        except Exception:
            cls._EMBEDDER_CACHE[key] = None
        return cls._EMBEDDER_CACHE[key]

    @staticmethod
    def _normalize_markdown(raw_markdown):
        text = str(raw_markdown or "").replace("\r\n", "\n").replace("\r", "\n")
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    @staticmethod
    def _tokenize_with_counts(text):
        source = str(text or "").lower()
        counts = Counter(re.findall(r"[a-z][a-z0-9_+-]{1,}", source))
        for block in re.findall(r"[\u4e00-\u9fff]{2,}", source):
            for idx in range(len(block) - 1):
                counts[block[idx : idx + 2]] += 1
        return counts

    @classmethod
    def _tokenize(cls, text):
        return set(cls._tokenize_with_counts(text))

    @classmethod
    def _infer_title(cls, text, source_path):
        for line in cls._normalize_markdown(text).split("\n"):
            stripped = line.strip()
            if re.match(r"^#{1,6}\s+", stripped):
                return stripped.lstrip("#").strip()
        stem = os.path.splitext(os.path.basename(str(source_path or "").strip()))[0]
        return stem or "Document"

    @classmethod
    def _normalize_documents(cls, documents):
        if isinstance(documents, str):
            items = [
                {
                    "source_path": "inline.md",
                    "title": cls._infer_title(documents, "inline.md"),
                    "text": documents,
                    "metadata": {"kind": "inline"},
                }
            ]
        else:
            items = list(documents or [])

        normalized = []
        seen = set()
        for index, item in enumerate(items, start=1):
            if isinstance(item, LiteratureDocument):
                source_path = os.path.abspath(item.source_path)
                text = cls._normalize_markdown(item.text)
                title = str(item.title or "").strip() or cls._infer_title(text, source_path)
                metadata = dict(item.metadata or {})
            elif isinstance(item, dict):
                source_path = os.path.abspath(str(item.get("source_path") or f"document_{index}.md"))
                text = cls._normalize_markdown(item.get("text", ""))
                if not text:
                    continue
                title = str(item.get("title", "")).strip() or cls._infer_title(text, source_path)
                metadata = dict(item.get("metadata") or {})
            else:
                continue

            content_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()
            dedupe_key = content_hash
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            doc_id = f"doc_{index:03d}_{content_hash[:10]}"
            normalized.append(
                LiteratureDocument(
                    doc_id=doc_id,
                    title=title,
                    source_path=source_path,
                    text=text,
                    metadata=metadata,
                )
            )
        return normalized

    @classmethod
    def _split_sections(cls, text):
        if not text:
            return []
        sections = []
        current_heading = "Document"
        current_lines = []
        for line in text.split("\n"):
            if re.match(r"^#{1,6}\s+", line.strip()):
                body = "\n".join(current_lines).strip()
                if body:
                    sections.append((current_heading, body))
                current_heading = line.strip()
                current_lines = []
                continue
            current_lines.append(line)
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_heading, body))
        return sections or [("Document", text)]

    @staticmethod
    def _paragraphs(body):
        items = [part.strip() for part in re.split(r"\n\s*\n", str(body or "").strip())]
        return [item for item in items if item]

    @staticmethod
    def _chunk_large_paragraph(paragraph, chunk_chars, overlap_chars):
        text = str(paragraph or "").strip()
        if not text:
            return []
        chunks = []
        start = 0
        length = len(text)
        while start < length:
            end = min(length, start + chunk_chars)
            chunks.append(text[start:end].strip())
            if end >= length:
                break
            start = max(end - overlap_chars, start + 1)
        return [chunk for chunk in chunks if chunk]

    @classmethod
    def _chunk_section(cls, heading, body, chunk_chars, overlap_chars):
        paragraphs = cls._paragraphs(body)
        if not paragraphs:
            return []

        chunks = []
        current = []
        current_len = 0

        def flush():
            nonlocal current, current_len
            if not current:
                return
            text = "\n\n".join(current).strip()
            if text:
                chunks.append((heading, text))
            if overlap_chars > 0 and current:
                overlap = []
                overlap_len = 0
                for paragraph in reversed(current):
                    overlap.insert(0, paragraph)
                    overlap_len += len(paragraph) + 2
                    if overlap_len >= overlap_chars:
                        break
                current = overlap
                current_len = sum(len(item) + 2 for item in current)
            else:
                current = []
                current_len = 0

        for paragraph in paragraphs:
            if len(paragraph) > chunk_chars * 2:
                flush()
                for piece in cls._chunk_large_paragraph(paragraph, chunk_chars, overlap_chars):
                    chunks.append((heading, piece))
                current = []
                current_len = 0
                continue

            paragraph_len = len(paragraph) + 2
            if current and current_len + paragraph_len > chunk_chars:
                flush()
            current.append(paragraph)
            current_len += paragraph_len

        flush()
        return chunks

    def _chunk_documents(self):
        rows = []
        for document in self.documents:
            sections = self._split_sections(document.text)
            chunk_counter = 0
            for section_index, (heading, body) in enumerate(sections, start=1):
                section_chunks = self._chunk_section(
                    heading,
                    body,
                    chunk_chars=self.chunk_chars,
                    overlap_chars=self.overlap_chars,
                )
                for chunk_index, (chunk_heading, chunk_body) in enumerate(section_chunks, start=1):
                    chunk_counter += 1
                    combined = f"{chunk_heading}\n{chunk_body}".strip()
                    rows.append(
                        {
                            "chunk_uid": f"{document.doc_id}_chunk_{section_index:03d}_{chunk_index:02d}",
                            "doc_id": document.doc_id,
                            "title": document.title,
                            "source_path": document.source_path,
                            "heading": chunk_heading,
                            "text": combined,
                            "token_counts": self._tokenize_with_counts(
                                f"{document.title}\n{chunk_heading}\n{combined}"
                            ),
                            "chunk_index": chunk_counter,
                        }
                    )
        return rows

    def _compute_idf(self, rows):
        chunk_total = max(1, len(rows))
        document_frequency = Counter()
        for row in rows:
            document_frequency.update(set((row.get("token_counts") or {}).keys()))
        self.idf_lookup = {
            token: math.log(1.0 + (chunk_total / (1.0 + freq))) + 1.0
            for token, freq in document_frequency.items()
        }

    @staticmethod
    def _hash_bucket(token, vector_dim):
        digest = hashlib.sha1(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % vector_dim

    def _encode_token_counts(self, token_counts):
        vector = np.zeros(self.vector_dim, dtype=np.float32)
        for token, count in (token_counts or {}).items():
            if not token or count <= 0:
                continue
            index = self._hash_bucket(token, self.vector_dim)
            weight = (1.0 + math.log(float(count))) * float(self.idf_lookup.get(token, 1.0))
            vector[index] += weight
        norm = float(np.linalg.norm(vector))
        if norm > 0.0:
            vector /= norm
        return vector

    def _encode_text(self, text, token_counts=None):
        normalized = self._normalize_markdown(text)
        if self._embedder is not None and normalized:
            try:
                vector = self._embedder.encode(
                    [normalized],
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                )[0]
                return np.asarray(vector, dtype=np.float32)
            except Exception:
                pass
        return self._encode_token_counts(token_counts or self._tokenize_with_counts(normalized))

    def _ensure_schema(self, conn):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rag_meta (
                meta_key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                text TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_uid TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                title TEXT NOT NULL,
                source_path TEXT NOT NULL,
                heading TEXT NOT NULL,
                text TEXT NOT NULL,
                vector BLOB NOT NULL,
                FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
            """
        )

    def _build_store_meta(self, rows):
        documents = [
            {
                "doc_id": document.doc_id,
                "title": document.title,
                "source_path": document.source_path,
                "content_hash": hashlib.sha1(document.text.encode("utf-8")).hexdigest(),
            }
            for document in self.documents
        ]
        payload = {
            "documents": documents,
            "chunk_chars": self.chunk_chars,
            "overlap_chars": self.overlap_chars,
            "embedding_model": self.embedding_model,
            "encoder_backend": self.encoder_backend,
            "vector_dim": self.vector_dim,
            "chunk_count": len(rows),
        }
        payload["corpus_signature"] = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return payload

    @staticmethod
    def _load_store_meta(conn):
        row = conn.execute(
            "SELECT value_json FROM rag_meta WHERE meta_key = ?",
            ("store_meta",),
        ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(str(row[0] or "{}"))
        except Exception:
            return {}

    @staticmethod
    def _write_store_meta(conn, payload):
        conn.execute(
            """
            INSERT INTO rag_meta (meta_key, value_json)
            VALUES (?, ?)
            ON CONFLICT(meta_key) DO UPDATE SET value_json=excluded.value_json
            """,
            ("store_meta", json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )

    def _load_chunks_from_store(self, conn):
        rows = conn.execute(
            """
            SELECT chunk_uid, doc_id, title, source_path, heading, text, vector
            FROM chunks
            ORDER BY doc_id ASC, chunk_index ASC
            """
        ).fetchall()
        self.chunks = [
            LiteratureChunk(
                chunk_uid=str(row["chunk_uid"]),
                doc_id=str(row["doc_id"]),
                title=str(row["title"]),
                source_path=str(row["source_path"]),
                heading=str(row["heading"]),
                text=str(row["text"]),
                vector=np.frombuffer(row["vector"], dtype=np.float32).copy(),
                score=0.0,
            )
            for row in rows
        ]

    def _rebuild_store(self, conn, rows, store_meta):
        for row in rows:
            row["vector"] = self._encode_text(
                f"{row['title']}\n{row['heading']}\n{row['text']}",
                token_counts=row.get("token_counts") or {},
            )

        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM documents")
        for document in self.documents:
            conn.execute(
                """
                INSERT INTO documents (doc_id, title, source_path, content_hash, metadata_json, text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    document.doc_id,
                    document.title,
                    document.source_path,
                    hashlib.sha1(document.text.encode("utf-8")).hexdigest(),
                    json.dumps(document.metadata or {}, ensure_ascii=False),
                    document.text,
                ),
            )
        for row in rows:
            conn.execute(
                """
                INSERT INTO chunks (chunk_uid, doc_id, chunk_index, title, source_path, heading, text, vector)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["chunk_uid"],
                    row["doc_id"],
                    row["chunk_index"],
                    row["title"],
                    row["source_path"],
                    row["heading"],
                    row["text"],
                    row["vector"].astype(np.float32).tobytes(),
                ),
            )
        self._write_store_meta(conn, store_meta)
        conn.commit()

        self.chunks = [
            LiteratureChunk(
                chunk_uid=row["chunk_uid"],
                doc_id=row["doc_id"],
                title=row["title"],
                source_path=row["source_path"],
                heading=row["heading"],
                text=row["text"],
                vector=row["vector"],
                score=0.0,
            )
            for row in rows
        ]

    def _load_or_rebuild_store(self):
        rows = self._chunk_documents()
        self._compute_idf(rows)
        store_meta = self._build_store_meta(rows)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            self._ensure_schema(conn)
            existing_meta = self._load_store_meta(conn)
            if (
                existing_meta.get("corpus_signature") == store_meta.get("corpus_signature")
                and int(existing_meta.get("chunk_count", 0) or 0) > 0
            ):
                self._load_chunks_from_store(conn)
                if self.chunks:
                    return
            self._rebuild_store(conn, rows, store_meta)
        finally:
            conn.close()

    def retrieve(self, query, top_k=4, max_chars=5000, per_document_limit=2):
        token_counts = self._tokenize_with_counts(query)
        if not token_counts or not self.chunks:
            return []

        query_vector = self._encode_text(query, token_counts=token_counts)
        if float(np.linalg.norm(query_vector)) <= 0.0:
            return []

        scored = []
        query_tokens = set(token_counts)
        for chunk in self.chunks:
            lexical_overlap = len(query_tokens & self._tokenize(chunk.heading))
            semantic_score = float(np.dot(query_vector, chunk.vector))
            total_score = semantic_score + 0.03 * float(lexical_overlap)
            if total_score <= 0.0:
                continue
            scored.append(
                LiteratureChunk(
                    chunk_uid=chunk.chunk_uid,
                    doc_id=chunk.doc_id,
                    title=chunk.title,
                    source_path=chunk.source_path,
                    heading=chunk.heading,
                    text=chunk.text,
                    vector=chunk.vector,
                    score=total_score,
                )
            )

        scored.sort(key=lambda item: (-item.score, item.doc_id, item.chunk_uid))

        selected = []
        used_chars = 0
        per_doc_counts = Counter()
        for chunk in scored:
            if top_k > 0 and len(selected) >= int(top_k):
                break
            if per_document_limit > 0 and per_doc_counts[chunk.doc_id] >= int(per_document_limit):
                continue
            if max_chars > 0 and used_chars >= int(max_chars):
                break
            text = chunk.text
            if max_chars > 0 and used_chars + len(text) > int(max_chars):
                remain = int(max_chars) - used_chars
                if remain < 200:
                    break
                text = text[:remain]
            selected.append(
                {
                    "chunk_uid": chunk.chunk_uid,
                    "doc_id": chunk.doc_id,
                    "title": chunk.title,
                    "source_path": chunk.source_path,
                    "heading": chunk.heading,
                    "score": round(chunk.score, 6),
                    "text": text.strip(),
                }
            )
            per_doc_counts[chunk.doc_id] += 1
            used_chars += len(text) + 2
        return selected

    def render(self, query, top_k=4, max_chars=5000, per_document_limit=2):
        items = self.retrieve(
            query,
            top_k=top_k,
            max_chars=max_chars,
            per_document_limit=per_document_limit,
        )
        if not items:
            return ""
        parts = []
        for item in items:
            parts.append(
                "\n".join(
                    [
                        (
                            f"### {item['chunk_uid']} | {item['title']} | "
                            f"{item['heading']} | score={item['score']}"
                        ),
                        f"Source: {item['source_path']}",
                        item["text"],
                    ]
                ).strip()
            )
        return "\n\n".join(parts).strip()
