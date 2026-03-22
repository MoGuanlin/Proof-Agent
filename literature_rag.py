import hashlib
import json
import os
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field

from app_config import (
    DISABLE_TEXT_TRUNCATION,
    LITERATURE_RAG_EMBEDDING_MODEL,
    LITERATURE_RAG_MAX_CHARS,
    LITERATURE_RAG_PER_DOCUMENT_LIMIT,
    LITERATURE_RAG_QDRANT_API_KEY,
    LITERATURE_RAG_QDRANT_COLLECTION,
    LITERATURE_RAG_QDRANT_PATH,
    LITERATURE_RAG_QDRANT_TIMEOUT,
    LITERATURE_RAG_QDRANT_URL,
    LITERATURE_RAG_RERANK_MODEL,
    LITERATURE_RAG_RETRIEVAL_CANDIDATES,
    LITERATURE_RAG_TOP_K,
    LITERATURE_RAG_VECTOR_DIM,
    VOYAGE_API_KEY,
    VOYAGE_API_TIMEOUT,
)

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qdrant_models
except Exception:  # pragma: no cover - optional dependency
    QdrantClient = None
    qdrant_models = None

try:
    import voyageai
except Exception:  # pragma: no cover - optional dependency
    voyageai = None


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
    score: float = 0.0


class LiteratureRAG:
    def __init__(
        self,
        documents,
        db_path,
        chunk_chars=1800,
        overlap_chars=240,
        vector_dim=LITERATURE_RAG_VECTOR_DIM,
        embedding_model=LITERATURE_RAG_EMBEDDING_MODEL,
    ):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.chunk_chars = max(400, int(chunk_chars))
        self.overlap_chars = max(0, int(overlap_chars))
        self.vector_dim = max(256, int(vector_dim))
        self.embedding_model = str(embedding_model or "").strip() or LITERATURE_RAG_EMBEDDING_MODEL
        self.rerank_model = str(LITERATURE_RAG_RERANK_MODEL or "").strip()
        self.retrieval_candidates = max(10, int(LITERATURE_RAG_RETRIEVAL_CANDIDATES or 40))
        self.documents = self._normalize_documents(documents)
        self.encoder_backend = "voyage_contextualized"
        self.qdrant_path = self._resolve_qdrant_path(self.db_path)
        self.collection_name = self._build_collection_name()
        self._qdrant = self._create_qdrant_client()
        self._voyage = self._create_voyage_client()
        self._load_or_rebuild_store()

    @staticmethod
    def _log(message):
        print(f"[LiteratureRAG] {message}", flush=True)

    @staticmethod
    def _normalize_markdown(raw_markdown):
        text = str(raw_markdown or "").replace("\r\n", "\n").replace("\r", "\n")
        return re.sub(r"\n{3,}", "\n\n", text).strip()

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
            if content_hash in seen:
                continue
            seen.add(content_hash)
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
        for paragraph in paragraphs:
            if len(paragraph) > chunk_chars * 2:
                for piece in cls._chunk_large_paragraph(paragraph, chunk_chars, overlap_chars):
                    chunks.append((heading, piece))
                continue
            chunks.append((heading, paragraph))
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
                            "chunk_index": chunk_counter,
                        }
                    )
        return rows

    @staticmethod
    def _sanitize_collection_name(raw_name):
        name = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(raw_name or "").strip()).strip("_")
        return name[:120] or "literature_rag"

    @classmethod
    def _resolve_qdrant_path(cls, db_path):
        configured = str(LITERATURE_RAG_QDRANT_PATH or "").strip()
        if configured:
            path = os.path.abspath(os.path.expanduser(configured))
        else:
            normalized = os.path.abspath(str(db_path or "").strip() or ".cache/literature_rag.sqlite")
            root, ext = os.path.splitext(normalized)
            path = f"{root}_qdrant" if ext else normalized
        os.makedirs(path, exist_ok=True)
        return path

    def _build_collection_name(self):
        return self._sanitize_collection_name(LITERATURE_RAG_QDRANT_COLLECTION or "literature_rag")

    def _create_qdrant_client(self):
        if QdrantClient is None or qdrant_models is None:
            raise RuntimeError(
                "qdrant-client is required for LiteratureRAG. Install it with `pip install qdrant-client`."
            )
        if str(LITERATURE_RAG_QDRANT_URL or "").strip():
            return QdrantClient(
                url=LITERATURE_RAG_QDRANT_URL,
                api_key=LITERATURE_RAG_QDRANT_API_KEY or None,
                timeout=LITERATURE_RAG_QDRANT_TIMEOUT,
            )
        return QdrantClient(path=self.qdrant_path, timeout=LITERATURE_RAG_QDRANT_TIMEOUT)

    def _create_voyage_client(self):
        if voyageai is None:
            raise RuntimeError(
                "voyageai is required for LiteratureRAG. Install it with `pip install voyageai`."
            )
        if not VOYAGE_API_KEY:
            raise RuntimeError("VOYAGE_API_KEY is required to use voyage-context-3 and rerank-2.5.")
        return voyageai.Client(api_key=VOYAGE_API_KEY, timeout=VOYAGE_API_TIMEOUT)

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
            "backend": self.encoder_backend,
            "documents": documents,
            "chunk_chars": self.chunk_chars,
            "overlap_chars": self.overlap_chars,
            "embedding_model": self.embedding_model,
            "rerank_model": self.rerank_model,
            "vector_dim": self.vector_dim,
            "chunk_count": len(rows),
        }
        payload["corpus_signature"] = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return payload

    def _get_collection_meta(self):
        if not self._qdrant.collection_exists(self.collection_name):
            return {}
        info = self._qdrant.get_collection(self.collection_name)
        config = getattr(info, "config", None)
        return dict(getattr(config, "metadata", None) or {})

    def _get_collection_info(self):
        if not self._qdrant.collection_exists(self.collection_name):
            return None
        return self._qdrant.get_collection(self.collection_name)

    @staticmethod
    def _compose_embedding_text(row):
        return f"{row['title']}\n{row['heading']}\n{row['text']}".strip()

    @staticmethod
    def _compose_rerank_text(item):
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or "").strip()
        return f"{title}\n{text}".strip() if title else text

    @staticmethod
    def _point_id(chunk_uid):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, str(chunk_uid or "").strip()))

    def _iter_embedding_batches(self, rows):
        batch = []
        batch_chars = 0
        for row in rows:
            row_chars = len(self._compose_embedding_text(row))
            exceeds = (
                batch
                and (
                    len(batch) >= 192
                    or batch_chars + row_chars > 160000
                )
            )
            if exceeds:
                yield batch
                batch = []
                batch_chars = 0
            batch.append(row)
            batch_chars += row_chars
        if batch:
            yield batch

    def _create_collection(self, store_meta):
        if self._qdrant.collection_exists(self.collection_name):
            self._log(f"删除旧 collection: {self.collection_name}")
            self._qdrant.delete_collection(self.collection_name)
        self._log(
            "创建新 collection: "
            f"name={self.collection_name} path={self.qdrant_path} dim={self.vector_dim} "
            f"docs={len(self.documents)} chunks={int(store_meta.get('chunk_count', 0) or 0)}"
        )
        self._qdrant.create_collection(
            self.collection_name,
            vectors_config=qdrant_models.VectorParams(
                size=self.vector_dim,
                distance=qdrant_models.Distance.COSINE,
            ),
            on_disk_payload=True,
            metadata=store_meta,
        )

    def _rebuild_store(self, rows, store_meta):
        self._create_collection(store_meta)
        batch_rows_list = list(self._iter_embedding_batches(rows))
        total_batches = len(batch_rows_list)
        total_points = 0
        for batch_index, batch_rows in enumerate(batch_rows_list, start=1):
            batch_doc_count = len({row["doc_id"] for row in batch_rows})
            batch_chunk_count = len(batch_rows)
            batch_chars = sum(len(self._compose_embedding_text(row)) for row in batch_rows)
            self._log(
                f"embedding batch {batch_index}/{total_batches}: "
                f"docs={batch_doc_count} chunks={batch_chunk_count} chars={batch_chars}"
            )
            inputs = [[self._compose_embedding_text(row)] for row in batch_rows]
            try:
                response = self._voyage.contextualized_embed(
                    inputs=inputs,
                    model=self.embedding_model,
                    input_type="document",
                    output_dimension=self.vector_dim,
                )
            except Exception as exc:
                self._log(f"embedding batch {batch_index}/{total_batches} 失败: {type(exc).__name__}: {exc}")
                raise
            points = []
            for row, group_result in zip(batch_rows, response.results):
                embeddings = list(group_result.embeddings or [])
                if len(embeddings) != 1:
                    raise RuntimeError("Voyage contextualized embeddings count does not match paragraph chunk count.")
                payload = {
                    "chunk_uid": row["chunk_uid"],
                    "doc_id": row["doc_id"],
                    "title": row["title"],
                    "source_path": row["source_path"],
                    "heading": row["heading"],
                    "text": row["text"],
                    "chunk_index": row["chunk_index"],
                }
                points.append(
                    qdrant_models.PointStruct(
                        id=self._point_id(row["chunk_uid"]),
                        vector=[float(x) for x in embeddings[0]],
                        payload=payload,
                    )
                )
            if points:
                self._qdrant.upsert(self.collection_name, points, wait=True)
                total_points += len(points)
                self._log(
                    f"upsert batch {batch_index}/{total_batches}: "
                    f"points={len(points)} total_points={total_points}"
                )
        self._log(f"建库完成: collection={self.collection_name} total_points={total_points}")

    def _load_or_rebuild_store(self):
        rows = self._chunk_documents()
        store_meta = self._build_store_meta(rows)
        collection_meta = self._get_collection_meta()
        collection_info = self._get_collection_info()
        existing_points = int(getattr(collection_info, "points_count", 0) or 0) if collection_info is not None else 0
        self._log(
            f"准备检查 collection: name={self.collection_name} path={self.qdrant_path} "
            f"docs={len(self.documents)} chunks={len(rows)} existing_points={existing_points}"
        )
        if (
            collection_meta.get("corpus_signature") == store_meta.get("corpus_signature")
            and int(collection_meta.get("chunk_count", 0) or 0) > 0
            and existing_points > 0
        ):
            self._log(
                f"命中现有向量库: collection={self.collection_name} "
                f"points={existing_points} chunks={int(collection_meta.get('chunk_count', 0) or 0)}"
            )
            return
        if collection_info is not None and existing_points <= 0:
            self._log(f"发现空 collection，将重建: collection={self.collection_name}")
        else:
            self._log(f"未命中可复用向量库，将重建: collection={self.collection_name}")
        self._rebuild_store(rows, store_meta)

    def _embed_query(self, query):
        response = self._voyage.contextualized_embed(
            inputs=[[str(query or "").strip()]],
            model=self.embedding_model,
            input_type="query",
            output_dimension=self.vector_dim,
        )
        if not response.results or not response.results[0].embeddings:
            return []
        return [float(x) for x in response.results[0].embeddings[0]]

    def _search_qdrant(self, query, limit):
        vector = self._embed_query(query)
        if not vector:
            return []
        response = self._qdrant.query_points(
            self.collection_name,
            query=vector,
            limit=max(1, int(limit)),
            with_payload=True,
            with_vectors=False,
        )
        items = []
        for point in response.points:
            payload = dict(point.payload or {})
            if not payload.get("text"):
                continue
            payload["score"] = float(point.score or 0.0)
            items.append(payload)
        return items

    def _rerank(self, query, items):
        if not items or not self.rerank_model:
            return items
        documents = [self._compose_rerank_text(item) for item in items]
        try:
            reranking = self._voyage.rerank(
                query=str(query or "").strip(),
                documents=documents,
                model=self.rerank_model,
                top_k=len(documents),
                truncation=True,
            )
        except Exception:
            return items

        ranked = []
        for result in reranking.results:
            item = dict(items[result.index])
            item["score"] = float(result.relevance_score)
            ranked.append(item)
        return ranked or items

    def retrieve(
        self,
        query,
        top_k=LITERATURE_RAG_TOP_K,
        max_chars=LITERATURE_RAG_MAX_CHARS,
        per_document_limit=LITERATURE_RAG_PER_DOCUMENT_LIMIT,
    ):
        if not str(query or "").strip():
            return []
        if DISABLE_TEXT_TRUNCATION:
            max_chars = 0

        top_k = int(top_k)
        candidate_limit = max(self.retrieval_candidates, top_k if top_k > 0 else 0)
        ranked = self._rerank(query, self._search_qdrant(query, candidate_limit))

        selected = []
        used_chars = 0
        per_doc_counts = Counter()
        for item in ranked:
            if top_k > 0 and len(selected) >= top_k:
                break
            doc_id = str(item.get("doc_id") or "")
            if per_document_limit > 0 and per_doc_counts[doc_id] >= int(per_document_limit):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            if max_chars > 0 and used_chars >= int(max_chars):
                break
            if max_chars > 0 and used_chars + len(text) > int(max_chars):
                remain = int(max_chars) - used_chars
                if remain < 200:
                    break
                text = text[:remain].strip()
            selected.append(
                {
                    "chunk_uid": str(item.get("chunk_uid") or ""),
                    "doc_id": doc_id,
                    "title": str(item.get("title") or ""),
                    "source_path": str(item.get("source_path") or ""),
                    "heading": str(item.get("heading") or ""),
                    "score": round(float(item.get("score") or 0.0), 6),
                    "text": text,
                }
            )
            per_doc_counts[doc_id] += 1
            used_chars += len(text) + 2
        return selected

    def render(
        self,
        query,
        top_k=LITERATURE_RAG_TOP_K,
        max_chars=LITERATURE_RAG_MAX_CHARS,
        per_document_limit=LITERATURE_RAG_PER_DOCUMENT_LIMIT,
    ):
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
