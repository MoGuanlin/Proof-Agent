"""Local-RAG literature injection for the relaxed agent.

The relaxed pipeline's original literature path is the Gemini Files API
(``attached_file_uris``), which is google-only. For OpenAI-compatible providers
(e.g. uuapi -> gpt5.5) we instead retrieve from the already-built local
Qdrant+Voyage store and inject the rendered snippets as a prompt prefix.

Retrieval is READ-ONLY: ``LiteratureRAG(..., rebuild=False)`` skips
load/rebuild and only queries the existing ``literature_rag`` collection, so an
empty ``documents`` list can never trigger a destructive rebuild. Any failure
(missing deps, Voyage unreachable, empty store) degrades to ``""`` and the run
proceeds contract-only.
"""
from __future__ import annotations

from ..app_config import (
    LITERATURE_RAG_DB_FILE,
    LITERATURE_RAG_EMBEDDING_MODEL,
    LITERATURE_RAG_TOP_K,
    LITERATURE_RAG_VECTOR_DIM,
)
from .obligations import get_contract

# Fixed domain anchors appended to every query so retrieval stays on-topic even
# when a contract's own text is terse.
_DOMAIN_KEYWORDS = (
    "Delaunay triangulation stretch factor upper bound; "
    "segment potential; terminal disk O_n; chord length L_n; "
    "endpoint case; path length |P|; |D|."
)


def build_obligation_query(oid: str) -> str:
    """Compose a retrieval query from the obligation contract."""
    contract = get_contract(oid)
    notes = " ".join(str(n) for n in contract.get("context_notes", []))
    return " ".join(
        part for part in (
            str(contract.get("title", "")),
            str(contract.get("claim", "")),
            notes,
            _DOMAIN_KEYWORDS,
        ) if part.strip()
    ).strip()


def retrieve_literature_packet(oid: str, *, top_k: int | None = None) -> str:
    """Return a rendered literature snippet packet for `oid`, or '' on failure.

    Read-only against the existing store; never rebuilds. Caller retrieves once
    per obligation and shares the packet across all agents.
    """
    try:
        from ..literature_rag import LiteratureRAG

        rag = LiteratureRAG(
            [],
            db_path=LITERATURE_RAG_DB_FILE,
            vector_dim=LITERATURE_RAG_VECTOR_DIM,
            embedding_model=LITERATURE_RAG_EMBEDDING_MODEL,
            rebuild=False,
        )
        return rag.render(
            build_obligation_query(oid),
            top_k=LITERATURE_RAG_TOP_K if top_k is None else int(top_k),
        ).strip()
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, run continues
        print(
            f"[literature] local RAG unavailable, degrading to contract-only: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return ""
