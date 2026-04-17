import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field

from .app_config import (
    DISABLE_TEXT_TRUNCATION,
    MEMORY_PROPERTY_PACKET_MAX_ITEMS,
    MEMORY_REUSE_MAX_ITEMS,
    MEMORY_SIMILAR_FAILURE_LIMIT,
    MEMORY_SUMMARIZE_MAX_CANDIDATES,
    MEMORY_TERMINAL_REPORT_MAX_ITEMS,
)


DEFAULT_PROPERTIES = ("N1", "N2", "N3", "D4", "Q5", "Q6")
PROPERTY_DEPENDENCIES = {
    "N1": "N1 失败会破坏 Lemma 2 的基础情形，因此后续局部递推不再可信。",
    "N2": "N2 失败意味着去末端盘单调性丢失，Prop 2 一般不可复用。",
    "N3": "N3 失败意味着内部分裂次可加性丢失，Prop 3 一般不可复用。",
    "D4": "D4 失败通常会破坏关于终点 v 的凸性/无关性论证，Prop 4/6 风险很高。",
    "Q5": "Q5 失败意味着局部目标函数导数控制不足，Prop 7 的数值/导数验证链断裂。",
    "Q6": "Q6 失败意味着极端链下界常数不可控，Lemma 3 无法给出改进后的全局界。",
}


@dataclass
class CandidateRecord:
    candidate_id: str
    form: str
    derived_from: str | None = None
    intuition: str = ""
    source_direction: str = ""
    property_status: dict[str, dict] = field(default_factory=dict)
    proposition_status: dict[str, dict] = field(default_factory=dict)
    reusable_props: list[str] = field(default_factory=list)
    needs_redo: list[str] = field(default_factory=list)
    priority: list[str] = field(default_factory=list)
    risk_notes: str = ""
    estimated_c: str = ""
    status: str = "active"
    pruned_reason: str = ""
    terminal_decision: dict[str, str] = field(default_factory=dict)
    tool_request_status: dict[str, dict[str, list[dict]]] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    exploration_log: list[dict] = field(default_factory=list)

    def ensure_properties(self, property_names=None):
        names = property_names or DEFAULT_PROPERTIES
        for name in names:
            self.property_status.setdefault(
                name,
                {"status": "untested", "note": "", "artifact_key": ""},
            )

    def mark_property(self, name, status, note="", artifact_key=""):
        self.ensure_properties([name])
        self.property_status[name] = {
            "status": str(status or "").strip() or "untested",
            "note": str(note or "").strip(),
            "artifact_key": str(artifact_key or "").strip(),
        }

    def ensure_property_propositions(self, property_name):
        key = str(property_name or "").strip()
        if not key:
            return
        self.proposition_status.setdefault(key, {"plan": [], "items": {}})
        bucket = self.proposition_status[key]
        bucket.setdefault("plan", [])
        bucket.setdefault("items", {})

    @staticmethod
    def _coerce_bool(value):
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def set_proposition_plan(self, property_name, propositions):
        self.ensure_property_propositions(property_name)
        bucket = self.proposition_status[property_name]
        plan = []
        items = bucket["items"]
        for index, proposition in enumerate(propositions or [], start=1):
            if not isinstance(proposition, dict):
                continue
            proposition_id = str(proposition.get("id", "")).strip() or f"{str(property_name).lower()}_prop_{index}"
            plan.append(proposition_id)
            entry = items.get(proposition_id, {})
            entry.update(
                {
                    "title": str(proposition.get("title", "")).strip(),
                    "claim": str(proposition.get("claim", "")).strip(),
                    "dependencies": [
                        str(dep).strip()
                        for dep in proposition.get("dependencies") or []
                        if str(dep).strip()
                    ],
                    "verification_focus": str(proposition.get("verification_focus", "")).strip(),
                    "requires_tool": self._coerce_bool(proposition.get("requires_tool")),
                    "tool_plan": dict(proposition.get("tool_plan") or entry.get("tool_plan") or {}),
                    "status": entry.get("status", "planned") or "planned",
                    "note": str(entry.get("note", "")).strip(),
                    "artifact_key": str(entry.get("artifact_key", "")).strip(),
                }
            )
            items[proposition_id] = entry
        bucket["plan"] = plan

    def mark_proposition(
        self,
        property_name,
        proposition_id,
        status,
        note="",
        artifact_key="",
        title="",
        claim="",
        dependencies=None,
        verification_focus="",
        requires_tool=None,
        tool_plan=None,
    ):
        self.ensure_property_propositions(property_name)
        pid = str(proposition_id or "").strip()
        if not pid:
            return
        bucket = self.proposition_status[property_name]
        if pid not in bucket["plan"]:
            bucket["plan"].append(pid)
        entry = bucket["items"].get(pid, {})
        entry.update(
            {
                "title": str(title or entry.get("title", "")).strip(),
                "claim": str(claim or entry.get("claim", "")).strip(),
                "dependencies": [
                    str(dep).strip()
                    for dep in (dependencies if dependencies is not None else entry.get("dependencies") or [])
                    if str(dep).strip()
                ],
                "verification_focus": str(verification_focus or entry.get("verification_focus", "")).strip(),
                "requires_tool": (
                    entry.get("requires_tool")
                    if requires_tool is None
                    else self._coerce_bool(requires_tool)
                ),
                "tool_plan": dict(entry.get("tool_plan") or {}) if tool_plan is None else dict(tool_plan or {}),
                "status": str(status or "").strip() or entry.get("status", "planned") or "planned",
                "note": str(note or "").strip(),
                "artifact_key": str(artifact_key or "").strip(),
            }
        )
        bucket["items"][pid] = entry

    def proposition_snapshot(self, property_name, max_items=6):
        bucket = self.proposition_status.get(str(property_name or "").strip()) or {}
        plan = bucket.get("plan") or []
        items = bucket.get("items") or {}
        bits = []
        for proposition_id in plan[: max(0, int(max_items)) or len(plan)]:
            detail = items.get(proposition_id) or {}
            bits.append(f"{proposition_id}={detail.get('status', 'planned')}")
        return ", ".join(bits)

    def proposition_items(self, property_name):
        bucket = self.proposition_status.get(str(property_name or "").strip()) or {}
        return dict(bucket.get("items") or {})

    def ensure_tool_request_bucket(self, property_name, proposition_id):
        prop = str(property_name or "").strip()
        pid = str(proposition_id or "").strip()
        if not prop or not pid:
            return []
        self.tool_request_status.setdefault(prop, {})
        self.tool_request_status[prop].setdefault(pid, [])
        return self.tool_request_status[prop][pid]

    def set_tool_requests(self, property_name, proposition_id, requests):
        bucket = self.ensure_tool_request_bucket(property_name, proposition_id)
        existing = {
            str(item.get("request_id", "")).strip(): dict(item)
            for item in bucket
            if isinstance(item, dict) and str(item.get("request_id", "")).strip()
        }
        normalized = []
        for index, item in enumerate(requests or [], start=1):
            if not isinstance(item, dict):
                continue
            request_id = str(item.get("request_id", "")).strip() or f"tool_{index}"
            entry = existing.get(request_id, {})
            entry.update(
                {
                    "request_id": request_id,
                    "tool_name": str(item.get("tool_name", "")).strip() or "verification",
                    "justification": str(item.get("justification", "")).strip(),
                    "spec": dict(item.get("spec") or {}),
                    "report": dict(entry.get("report") or {}),
                    "report_status": str(entry.get("report_status", "")).strip(),
                    "artifact_key": str(entry.get("artifact_key", "")).strip(),
                }
            )
            normalized.append(entry)
        self.tool_request_status.setdefault(str(property_name or "").strip(), {})[str(proposition_id or "").strip()] = normalized

    def record_tool_result(
        self,
        property_name,
        proposition_id,
        request_id,
        tool_name,
        justification="",
        spec=None,
        report=None,
        artifact_key="",
    ):
        bucket = self.ensure_tool_request_bucket(property_name, proposition_id)
        rid = str(request_id or "").strip()
        if not rid:
            return
        matched = None
        for item in bucket:
            if str((item or {}).get("request_id", "")).strip() == rid:
                matched = item
                break
        if matched is None:
            matched = {}
            bucket.append(matched)
        report_payload = dict(report or {})
        matched.update(
            {
                "request_id": rid,
                "tool_name": str(tool_name or "").strip() or "verification",
                "justification": str(justification or "").strip(),
                "spec": dict(spec or {}),
                "report": report_payload,
                "report_status": str(report_payload.get("status", "")).strip(),
                "artifact_key": str(artifact_key or "").strip(),
            }
        )

    def tool_request_items(self, property_name, proposition_id):
        prop = str(property_name or "").strip()
        pid = str(proposition_id or "").strip()
        return list((self.tool_request_status.get(prop) or {}).get(pid) or [])

    def set_terminal_decision(self, action="", rationale="", next_direction="", stage=""):
        self.terminal_decision = {
            "action": str(action or "").strip(),
            "rationale": str(rationale or "").strip(),
            "next_direction": str(next_direction or "").strip(),
            "stage": str(stage or "").strip(),
        }

    def mark_pruned(self, reason):
        self.status = "pruned"
        self.pruned_reason = str(reason or "").strip()

    def mark_passed(self):
        self.status = "passed"
        self.pruned_reason = ""

    def append_log(self, stage, message, **extra):
        entry = {"stage": str(stage or "").strip(), "message": str(message or "").strip()}
        for key, value in extra.items():
            if value is None:
                continue
            entry[str(key)] = value
        self.exploration_log.append(entry)

    def to_dict(self):
        self.ensure_properties()
        return asdict(self)

    @classmethod
    def from_dict(cls, payload):
        data = dict(payload or {})
        raw_derived_from = data.get("derived_from")
        record = cls(
            candidate_id=str(data.get("candidate_id", "")).strip(),
            form=str(data.get("form", "")).strip(),
            derived_from=None if raw_derived_from is None else str(raw_derived_from).strip() or None,
            intuition=str(data.get("intuition", "")).strip(),
            source_direction=str(data.get("source_direction", "")).strip(),
            property_status=dict(data.get("property_status") or {}),
            proposition_status=dict(data.get("proposition_status") or {}),
            reusable_props=[str(x).strip() for x in data.get("reusable_props") or [] if str(x).strip()],
            needs_redo=[str(x).strip() for x in data.get("needs_redo") or [] if str(x).strip()],
            priority=[str(x).strip() for x in data.get("priority") or [] if str(x).strip()],
            risk_notes=str(data.get("risk_notes", "")).strip(),
            estimated_c=str(data.get("estimated_c", "")).strip(),
            status=str(data.get("status", "active")).strip() or "active",
            pruned_reason=str(data.get("pruned_reason", "")).strip(),
            terminal_decision={
                str(k): str(v)
                for k, v in (data.get("terminal_decision") or {}).items()
            },
            tool_request_status={
                str(prop): {
                    str(pid): list(items or [])
                    for pid, items in (bucket or {}).items()
                }
                for prop, bucket in (data.get("tool_request_status") or {}).items()
            },
            artifacts={str(k): str(v) for k, v in (data.get("artifacts") or {}).items()},
            exploration_log=list(data.get("exploration_log") or []),
        )
        record.ensure_properties()
        return record


class MemoryManager:
    @staticmethod
    def _truncate_text(text, max_chars=0):
        rendered = str(text or "").strip()
        if DISABLE_TEXT_TRUNCATION:
            return rendered
        if max_chars > 0 and len(rendered) > max_chars:
            return rendered[:max_chars]
        return rendered

    def __init__(self, store_path):
        requested_path = os.path.abspath(store_path)
        root, ext = os.path.splitext(requested_path)
        if ext.lower() == ".jsonl":
            self.legacy_jsonl_path = requested_path
            self.store_path = root + ".sqlite"
        else:
            sibling_jsonl = root + ".jsonl"
            self.legacy_jsonl_path = sibling_jsonl if os.path.exists(sibling_jsonl) else ""
            self.store_path = requested_path
        self.legacy_archive_path = root + ".legacy.jsonl"
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        self._initialize_store()

    def _connect(self):
        conn = sqlite3.connect(self.store_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize_store(self):
        with self._connect() as conn:
            self._ensure_schema(conn)
        self._maybe_import_legacy_jsonl()
        self._archive_legacy_jsonl_if_safe()
        self._warn_if_candidate_id_conflicts()

    def _ensure_schema(self, conn):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS candidate_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id TEXT NOT NULL,
                saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,
                derived_from TEXT,
                form TEXT NOT NULL,
                source_direction TEXT,
                estimated_c TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_candidate_snapshots_candidate
                ON candidate_snapshots(candidate_id, snapshot_id DESC);

            CREATE TABLE IF NOT EXISTS candidate_latest (
                candidate_id TEXT PRIMARY KEY,
                snapshot_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                derived_from TEXT,
                form TEXT NOT NULL,
                FOREIGN KEY(snapshot_id) REFERENCES candidate_snapshots(snapshot_id)
            );

            CREATE TABLE IF NOT EXISTS property_states (
                snapshot_id INTEGER NOT NULL,
                candidate_id TEXT NOT NULL,
                property_name TEXT NOT NULL,
                status TEXT NOT NULL,
                note TEXT,
                artifact_key TEXT,
                PRIMARY KEY(snapshot_id, property_name),
                FOREIGN KEY(snapshot_id) REFERENCES candidate_snapshots(snapshot_id)
            );
            CREATE INDEX IF NOT EXISTS idx_property_states_lookup
                ON property_states(property_name, status, snapshot_id DESC);

            CREATE TABLE IF NOT EXISTS proposition_states (
                snapshot_id INTEGER NOT NULL,
                candidate_id TEXT NOT NULL,
                property_name TEXT NOT NULL,
                proposition_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                title TEXT,
                claim TEXT,
                dependencies_json TEXT,
                verification_focus TEXT,
                requires_tool INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                note TEXT,
                artifact_key TEXT,
                signature TEXT,
                PRIMARY KEY(snapshot_id, property_name, proposition_id),
                FOREIGN KEY(snapshot_id) REFERENCES candidate_snapshots(snapshot_id)
            );
            CREATE INDEX IF NOT EXISTS idx_proposition_states_lookup
                ON proposition_states(property_name, status, requires_tool, snapshot_id DESC);

            CREATE TABLE IF NOT EXISTS artifacts (
                snapshot_id INTEGER NOT NULL,
                candidate_id TEXT NOT NULL,
                artifact_key TEXT NOT NULL,
                content TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, artifact_key),
                FOREIGN KEY(snapshot_id) REFERENCES candidate_snapshots(snapshot_id)
            );
            CREATE INDEX IF NOT EXISTS idx_artifacts_lookup
                ON artifacts(candidate_id, artifact_key, snapshot_id DESC);

            CREATE TABLE IF NOT EXISTS tool_request_states (
                snapshot_id INTEGER NOT NULL,
                candidate_id TEXT NOT NULL,
                property_name TEXT NOT NULL,
                proposition_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                justification TEXT,
                spec_json TEXT NOT NULL,
                report_json TEXT NOT NULL,
                report_status TEXT,
                artifact_key TEXT,
                PRIMARY KEY(snapshot_id, property_name, proposition_id, request_id),
                FOREIGN KEY(snapshot_id) REFERENCES candidate_snapshots(snapshot_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tool_request_states_lookup
                ON tool_request_states(property_name, proposition_id, report_status, snapshot_id DESC);
            """
        )

    def _maybe_import_legacy_jsonl(self):
        if not self.legacy_jsonl_path or not os.path.exists(self.legacy_jsonl_path):
            return
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM candidate_snapshots").fetchone()[0]
            if count:
                return
            imported = 0
            with open(self.legacy_jsonl_path, "r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    record = CandidateRecord.from_dict(payload)
                    self._save_candidate_record(conn, record)
                    imported += 1
            if imported:
                conn.commit()

    def _archive_legacy_jsonl_if_safe(self):
        if not self.legacy_jsonl_path or not os.path.exists(self.legacy_jsonl_path):
            return
        with self._connect() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM candidate_snapshots").fetchone()[0] or 0)
        if count <= 0:
            return

        archive_path = self.legacy_archive_path
        if os.path.exists(archive_path):
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            archive_path = archive_path.replace(".legacy.jsonl", f".{timestamp}.legacy.jsonl")
        os.replace(self.legacy_jsonl_path, archive_path)
        self.legacy_jsonl_path = archive_path

    @staticmethod
    def _tokenize(text):
        source = str(text or "").lower()
        tokens = set(re.findall(r"[a-z][a-z0-9_+-]{1,}", source))
        for block in re.findall(r"[\u4e00-\u9fff]{2,}", source):
            for idx in range(len(block) - 1):
                tokens.add(block[idx : idx + 2])
        return tokens

    @staticmethod
    def _extract_markdown_section(md_text, section_title):
        match = re.search(
            rf"(?ims)^##\s*{re.escape(section_title)}\s*$([\s\S]*?)(?=^##\s+|\Z)",
            str(md_text or ""),
        )
        return match.group(1).strip() if match else ""

    @staticmethod
    def _normalize_proposition_signature(property_name, entry):
        source = " ".join(
            [
                str(property_name or "").strip(),
                str((entry or {}).get("title", "")).strip(),
                str((entry or {}).get("verification_focus", "")).strip(),
                "tool" if (entry or {}).get("requires_tool") else "plain",
            ]
        ).lower()
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", source)
        tokens = [token for token in normalized.split() if token]
        return " ".join(tokens[:12]).strip() or str(property_name or "").strip().lower() or "proposition"

    @staticmethod
    def _canonical_payload_json(payload):
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                return str(payload).strip()
        return json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _normalize_candidate_id(candidate_id, fallback_prefix="candidate"):
        cleaned = re.sub(r"\s+", "_", str(candidate_id or "").strip())
        cleaned = re.sub(r"[^A-Za-z0-9_:-]+", "_", cleaned).strip("_")
        return cleaned or str(fallback_prefix or "candidate").strip() or "candidate"

    def _candidate_id_exists_conn(self, conn, candidate_id):
        row = conn.execute(
            "SELECT 1 FROM candidate_snapshots WHERE candidate_id = ? LIMIT 1",
            (str(candidate_id or "").strip(),),
        ).fetchone()
        return bool(row)

    def candidate_id_exists(self, candidate_id):
        normalized = self._normalize_candidate_id(candidate_id)
        with self._connect() as conn:
            return self._candidate_id_exists_conn(conn, normalized)

    def make_unique_candidate_id(self, candidate_id, fallback_prefix="candidate"):
        base = self._normalize_candidate_id(candidate_id, fallback_prefix=fallback_prefix)
        with self._connect() as conn:
            if not self._candidate_id_exists_conn(conn, base):
                return base
            suffix = 2
            while True:
                candidate = f"{base}__v{suffix}"
                if not self._candidate_id_exists_conn(conn, candidate):
                    return candidate
                suffix += 1

    def _warn_if_candidate_id_conflicts(self):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT candidate_id, COUNT(DISTINCT TRIM(form)) AS form_count, COUNT(*) AS snapshot_count
                FROM candidate_snapshots
                GROUP BY candidate_id
                HAVING COUNT(DISTINCT TRIM(form)) > 1
                ORDER BY snapshot_count DESC, candidate_id ASC
                LIMIT 20
                """
            ).fetchall()
        if not rows:
            return
        rendered = ", ".join(
            f"{str(row['candidate_id'])}[forms={int(row['form_count'])}, snapshots={int(row['snapshot_count'])}]"
            for row in rows
        )
        print(
            "[MemoryManager] Warning: existing candidate_id collisions with multiple forms were detected. "
            "Historical memory may already be polluted for: "
            f"{rendered}",
            flush=True,
        )

    def _save_candidate_record(self, conn, candidate):
        record = candidate if isinstance(candidate, CandidateRecord) else CandidateRecord.from_dict(candidate)
        record.ensure_properties()
        payload = record.to_dict()
        payload_json = self._canonical_payload_json(payload)
        latest = conn.execute(
            """
            SELECT cs.snapshot_id, cs.payload_json
            FROM candidate_latest cl
            JOIN candidate_snapshots cs ON cs.snapshot_id = cl.snapshot_id
            WHERE cl.candidate_id = ?
            """,
            (record.candidate_id,),
        ).fetchone()
        if latest is not None:
            existing_payload = self._canonical_payload_json(str(latest["payload_json"] or ""))
            if existing_payload == payload_json:
                return int(latest["snapshot_id"])
        cursor = conn.execute(
            """
            INSERT INTO candidate_snapshots (
                candidate_id, status, derived_from, form, source_direction, estimated_c, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.candidate_id,
                record.status,
                record.derived_from,
                record.form,
                record.source_direction,
                record.estimated_c,
                payload_json,
            ),
        )
        snapshot_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO candidate_latest (candidate_id, snapshot_id, status, derived_from, form)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                snapshot_id=excluded.snapshot_id,
                status=excluded.status,
                derived_from=excluded.derived_from,
                form=excluded.form
            """,
            (
                record.candidate_id,
                snapshot_id,
                record.status,
                record.derived_from,
                record.form,
            ),
        )

        for property_name, detail in record.property_status.items():
            detail = detail or {}
            conn.execute(
                """
                INSERT INTO property_states (snapshot_id, candidate_id, property_name, status, note, artifact_key)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    record.candidate_id,
                    property_name,
                    str(detail.get("status", "")).strip() or "untested",
                    str(detail.get("note", "")).strip(),
                    str(detail.get("artifact_key", "")).strip(),
                ),
            )

        for property_name, bucket in record.proposition_status.items():
            bucket = bucket or {}
            plan = list(bucket.get("plan") or [])
            items = dict(bucket.get("items") or {})
            for position, proposition_id in enumerate(plan, start=1):
                entry = items.get(proposition_id) or {}
                conn.execute(
                    """
                    INSERT INTO proposition_states (
                        snapshot_id, candidate_id, property_name, proposition_id, position,
                        title, claim, dependencies_json, verification_focus, requires_tool,
                        status, note, artifact_key, signature
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        record.candidate_id,
                        property_name,
                        proposition_id,
                        position,
                        str(entry.get("title", "")).strip(),
                        str(entry.get("claim", "")).strip(),
                        json.dumps(entry.get("dependencies") or [], ensure_ascii=False),
                        str(entry.get("verification_focus", "")).strip(),
                        1 if bool(entry.get("requires_tool")) else 0,
                        str(entry.get("status", "planned")).strip() or "planned",
                        str(entry.get("note", "")).strip(),
                        str(entry.get("artifact_key", "")).strip(),
                        self._normalize_proposition_signature(property_name, entry),
                    ),
                )

        for artifact_key, content in record.artifacts.items():
            conn.execute(
                """
                INSERT INTO artifacts (snapshot_id, candidate_id, artifact_key, content)
                VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    record.candidate_id,
                    str(artifact_key).strip(),
                    str(content or ""),
                ),
            )

        for property_name, proposition_bucket in (record.tool_request_status or {}).items():
            for proposition_id, items in (proposition_bucket or {}).items():
                for position, item in enumerate(items or [], start=1):
                    if not isinstance(item, dict):
                        continue
                    conn.execute(
                        """
                        INSERT INTO tool_request_states (
                            snapshot_id, candidate_id, property_name, proposition_id, request_id, position,
                            tool_name, justification, spec_json, report_json, report_status, artifact_key
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot_id,
                            record.candidate_id,
                            str(property_name).strip(),
                            str(proposition_id).strip(),
                            str(item.get("request_id", "")).strip() or f"tool_{position}",
                            position,
                            str(item.get("tool_name", "")).strip() or "verification",
                            str(item.get("justification", "")).strip(),
                            json.dumps(item.get("spec") or {}, ensure_ascii=False),
                            json.dumps(item.get("report") or {}, ensure_ascii=False),
                            str(item.get("report_status", "")).strip(),
                            str(item.get("artifact_key", "")).strip(),
                        ),
                    )
        return snapshot_id

    def save_candidate(self, candidate):
        with self._connect() as conn:
            snapshot_id = self._save_candidate_record(conn, candidate)
            conn.commit()
        return snapshot_id

    @staticmethod
    def _row_to_candidate(row):
        return CandidateRecord.from_dict(json.loads(str(row["payload_json"])))

    @staticmethod
    def _normalize_status_filter(status_filter):
        if status_filter is None:
            return []
        if isinstance(status_filter, str):
            items = [status_filter]
        else:
            items = list(status_filter)
        return [str(item).strip().lower() for item in items if str(item).strip()]

    def query_candidate_library(self, status_filter=None, property_name=None, property_status=None, limit=0):
        clauses = []
        params = []
        sql = [
            "SELECT cs.snapshot_id, cs.payload_json",
            "FROM candidate_latest cl",
            "JOIN candidate_snapshots cs ON cs.snapshot_id = cl.snapshot_id",
        ]
        if property_name:
            sql.append(
                "JOIN property_states ps ON ps.snapshot_id = cs.snapshot_id AND ps.candidate_id = cs.candidate_id"
            )
            clauses.append("ps.property_name = ?")
            params.append(str(property_name).strip())
            if property_status:
                clauses.append("LOWER(ps.status) = ?")
                params.append(str(property_status).strip().lower())
        statuses = self._normalize_status_filter(status_filter)
        if statuses:
            clauses.append("LOWER(cs.status) IN (%s)" % ",".join("?" for _ in statuses))
            params.extend(statuses)
        if clauses:
            sql.append("WHERE " + " AND ".join(clauses))
        sql.append("ORDER BY cs.snapshot_id DESC")
        if limit and int(limit) > 0:
            sql.append("LIMIT ?")
            params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute("\n".join(sql), params).fetchall()
        return [self._row_to_candidate(row) for row in rows]

    def load_latest_candidates(self):
        records = self.query_candidate_library(limit=0)
        records.reverse()
        return records

    def query_proposition_library(self, property_name, status_filter=None, requires_tool=None, limit=0):
        prop = str(property_name or "").strip()
        if not prop:
            return []
        clauses = ["ps.property_name = ?"]
        params = [prop]
        statuses = self._normalize_status_filter(status_filter)
        if statuses:
            clauses.append("LOWER(ps.status) IN (%s)" % ",".join("?" for _ in statuses))
            params.extend(statuses)
        if requires_tool is not None:
            clauses.append("ps.requires_tool = ?")
            params.append(1 if bool(requires_tool) else 0)

        sql = [
            """
            SELECT
                ps.snapshot_id,
                ps.candidate_id,
                ps.property_name,
                ps.proposition_id,
                ps.position,
                ps.title,
                ps.claim,
                ps.dependencies_json,
                ps.verification_focus,
                ps.requires_tool,
                ps.status,
                ps.note,
                ps.artifact_key,
                ps.signature,
                cs.form,
                cs.status AS candidate_status,
                cs.derived_from,
                a.content AS artifact_content
            FROM proposition_states ps
            JOIN candidate_latest cl
                ON cl.candidate_id = ps.candidate_id AND cl.snapshot_id = ps.snapshot_id
            JOIN candidate_snapshots cs
                ON cs.snapshot_id = ps.snapshot_id
            LEFT JOIN artifacts a
                ON a.snapshot_id = ps.snapshot_id AND a.artifact_key = ps.artifact_key
            WHERE
            """
            + " AND ".join(clauses)
            + """
            ORDER BY ps.snapshot_id DESC, ps.position ASC
            """
        ]
        if limit and int(limit) > 0:
            sql.append("LIMIT ?")
            params.append(int(limit))

        with self._connect() as conn:
            rows = conn.execute("\n".join(sql), params).fetchall()
        items = []
        for row in rows:
            items.append(
                {
                    "snapshot_id": int(row["snapshot_id"]),
                    "candidate_id": str(row["candidate_id"]),
                    "property_name": str(row["property_name"]),
                    "proposition_id": str(row["proposition_id"]),
                    "position": int(row["position"]),
                    "title": str(row["title"] or "").strip(),
                    "claim": str(row["claim"] or "").strip(),
                    "dependencies": json.loads(str(row["dependencies_json"] or "[]")),
                    "verification_focus": str(row["verification_focus"] or "").strip(),
                    "requires_tool": bool(row["requires_tool"]),
                    "status": str(row["status"] or "").strip(),
                    "note": str(row["note"] or "").strip(),
                    "artifact_key": str(row["artifact_key"] or "").strip(),
                    "signature": str(row["signature"] or "").strip(),
                    "form": str(row["form"] or "").strip(),
                    "candidate_status": str(row["candidate_status"] or "").strip(),
                    "derived_from": str(row["derived_from"] or "").strip(),
                    "artifact_content": str(row["artifact_content"] or ""),
                }
            )
        return items

    def query_tool_request_library(
        self,
        property_name=None,
        proposition_id=None,
        report_status=None,
        tool_name=None,
        limit=0,
    ):
        clauses = ["1=1"]
        params = []
        if property_name:
            clauses.append("trs.property_name = ?")
            params.append(str(property_name).strip())
        if proposition_id:
            clauses.append("trs.proposition_id = ?")
            params.append(str(proposition_id).strip())
        if report_status:
            clauses.append("LOWER(trs.report_status) = ?")
            params.append(str(report_status).strip().lower())
        if tool_name:
            clauses.append("LOWER(trs.tool_name) = ?")
            params.append(str(tool_name).strip().lower())

        sql = [
            """
            SELECT
                trs.snapshot_id,
                trs.candidate_id,
                trs.property_name,
                trs.proposition_id,
                trs.request_id,
                trs.position,
                trs.tool_name,
                trs.justification,
                trs.spec_json,
                trs.report_json,
                trs.report_status,
                trs.artifact_key,
                cs.form,
                cs.status AS candidate_status
            FROM tool_request_states trs
            JOIN candidate_latest cl
                ON cl.candidate_id = trs.candidate_id AND cl.snapshot_id = trs.snapshot_id
            JOIN candidate_snapshots cs
                ON cs.snapshot_id = trs.snapshot_id
            WHERE
            """
            + " AND ".join(clauses)
            + """
            ORDER BY trs.snapshot_id DESC, trs.position ASC
            """
        ]
        if limit and int(limit) > 0:
            sql.append("LIMIT ?")
            params.append(int(limit))

        with self._connect() as conn:
            rows = conn.execute("\n".join(sql), params).fetchall()
        return [
            {
                "snapshot_id": int(row["snapshot_id"]),
                "candidate_id": str(row["candidate_id"]),
                "property_name": str(row["property_name"]),
                "proposition_id": str(row["proposition_id"]),
                "request_id": str(row["request_id"]),
                "position": int(row["position"]),
                "tool_name": str(row["tool_name"] or "").strip(),
                "justification": str(row["justification"] or "").strip(),
                "spec": json.loads(str(row["spec_json"] or "{}")),
                "report": json.loads(str(row["report_json"] or "{}")),
                "report_status": str(row["report_status"] or "").strip(),
                "artifact_key": str(row["artifact_key"] or "").strip(),
                "form": str(row["form"] or "").strip(),
                "candidate_status": str(row["candidate_status"] or "").strip(),
            }
            for row in rows
        ]

    def find_similar_failures(self, form_text, limit=MEMORY_SIMILAR_FAILURE_LIMIT, property_name=None):
        query = self._tokenize(form_text)
        scored = []
        for record in self.query_candidate_library(
            status_filter={"pruned"},
            property_name=property_name,
            property_status="fail" if property_name else None,
            limit=0,
        ):
            score = len(query & self._tokenize(record.form))
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in scored[: max(0, int(limit))]]

    def aggregate_failure_patterns(self):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ps.property_name, COUNT(*) AS fail_count
                FROM property_states ps
                JOIN candidate_latest cl
                    ON cl.candidate_id = ps.candidate_id AND cl.snapshot_id = ps.snapshot_id
                WHERE LOWER(ps.status) = 'fail'
                GROUP BY ps.property_name
                ORDER BY ps.property_name ASC
                """
            ).fetchall()
        return {str(row["property_name"]): int(row["fail_count"]) for row in rows}

    def build_derived_tree(self):
        nodes = {}
        children = {}
        for record in self.load_latest_candidates():
            nodes[record.candidate_id] = record
            parent = record.derived_from or ""
            if parent:
                children.setdefault(parent, []).append(record.candidate_id)
        for key in children:
            children[key].sort()
        return {"nodes": nodes, "children": children}

    def recent_terminal_candidates(self, limit=6):
        items = self.query_candidate_library(status_filter={"pruned", "passed"}, limit=limit or 0)
        items.reverse()
        if limit and len(items) > int(limit):
            items = items[-int(limit) :]
        return items

    def dependency_knowledge_summary(self, max_items=6):
        lines = []
        for prop in DEFAULT_PROPERTIES[: max(0, int(max_items)) or len(DEFAULT_PROPERTIES)]:
            note = PROPERTY_DEPENDENCIES.get(prop, "")
            if note:
                lines.append(f"- {prop}: {note}")
        return "\n".join(lines).strip()

    def heuristic_summary(self, max_items=6):
        recent = self.recent_terminal_candidates(limit=max_items * 2 or 6)
        lines = []
        seen = set()
        for record in reversed(recent):
            if record.status != "pruned":
                continue
            fail_props = []
            for prop in DEFAULT_PROPERTIES:
                detail = record.property_status.get(prop) or {}
                if str(detail.get("status", "")).strip().lower() == "fail":
                    fail_props.append((prop, str(detail.get("note", "")).strip()))
            for prop, note in fail_props:
                heuristic = f"避免重复 {record.candidate_id} 在 {prop} 上的失败模式"
                if note:
                    heuristic += f"：{note}"
                if heuristic in seen:
                    continue
                seen.add(heuristic)
                lines.append(f"- {heuristic}")
                if len(lines) >= max_items:
                    return "\n".join(lines).strip()
        if not lines:
            return "- 暂无可提炼的失败启发式。"
        return "\n".join(lines).strip()

    def proposition_history_summary(self, property_name, status_filter=None, max_items=6):
        want_status = set(self._normalize_status_filter(status_filter))
        if not str(property_name or "").strip():
            return ""
        lines = []
        seen = set()
        for entry in self.query_proposition_library(property_name, status_filter=want_status, limit=max_items * 8 or 12):
            status = str(entry.get("status", "")).strip().lower()
            if want_status and status not in want_status:
                continue
            if not status or status in {"planned", "in_progress", "untested"}:
                continue
            title = str(entry.get("title", "")).strip() or entry["proposition_id"]
            focus = str(entry.get("verification_focus", "")).strip() or "[未写 focus]"
            note = str(entry.get("note", "")).strip() or "[无 note]"
            signature = (status, title, focus, note)
            if signature in seen:
                continue
            seen.add(signature)
            lines.append(
                f"- {entry['candidate_id']}/{entry['proposition_id']}: status={status}; "
                f"title={title}; focus={focus}; note={note}"
            )
            if len(lines) >= max_items:
                break
        return "\n".join(lines).strip()

    def tool_request_history_summary(self, property_name, report_status=None, max_items=6):
        prop = str(property_name or "").strip()
        if not prop:
            return ""
        want_status = self._normalize_status_filter([report_status] if report_status else None)
        lines = []
        seen = set()
        for entry in self.query_tool_request_library(property_name=prop, limit=max_items * 8 or 12):
            status = str(entry.get("report_status", "")).strip().lower()
            if want_status and status not in want_status:
                continue
            if not status:
                continue
            spec = entry.get("spec") or {}
            report = entry.get("report") or {}
            signature = (
                entry.get("tool_name", ""),
                spec.get("mode", ""),
                status,
                str(report.get("summary", "")).strip(),
            )
            if signature in seen:
                continue
            seen.add(signature)
            lines.append(
                f"- {entry['candidate_id']}/{entry['proposition_id']}/{entry['request_id']}: "
                f"tool={entry.get('tool_name', 'unknown')}; mode={spec.get('mode', '[none]')}; "
                f"status={status}; summary={str(report.get('summary', '')).strip() or '[none]'}"
            )
            if len(lines) >= max_items:
                break
        return "\n".join(lines).strip()

    def reusable_proposition_examples(
        self,
        property_name,
        form_text="",
        max_items=MEMORY_REUSE_MAX_ITEMS,
    ):
        prop = str(property_name or "").strip()
        if not prop:
            return []
        query_tokens = self._tokenize(form_text)
        candidates = []
        for entry in self.query_proposition_library(prop, status_filter={"pass"}, limit=max_items * 12 or 24):
            artifact = str(entry.get("artifact_content", ""))
            claim = str(entry.get("claim", "")).strip() or self._extract_markdown_section(artifact, "Claim")
            conclusion = self._extract_markdown_section(artifact, "Conclusion") or str(entry.get("note", "")).strip()
            derivation = self._extract_markdown_section(artifact, "Derivation")
            derivation_lines = [
                line.strip()
                for line in derivation.splitlines()
                if line.strip()
            ][:3]
            focus = str(entry.get("verification_focus", "")).strip()
            score = len(query_tokens & self._tokenize(entry.get("form", "")))
            score += len(query_tokens & self._tokenize(" ".join([claim, focus])))
            if entry.get("candidate_status") == "passed":
                score += 1
            candidates.append(
                (
                    score,
                    entry["candidate_id"],
                    {
                        "candidate_id": entry["candidate_id"],
                        "proposition_id": entry["proposition_id"],
                        "signature": entry.get("signature", "") or self._normalize_proposition_signature(prop, entry),
                        "title": str(entry.get("title", "")).strip() or entry["proposition_id"],
                        "claim": claim or "[缺失]",
                        "verification_focus": focus or "[缺失]",
                        "conclusion": conclusion or "[缺失]",
                        "derivation_hint": " | ".join(derivation_lines) or "[缺失]",
                        "requires_tool": bool(entry.get("requires_tool")),
                    },
                )
            )
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected = []
        seen = set()
        for _, _, payload in candidates:
            signature = payload["signature"]
            if signature in seen:
                continue
            seen.add(signature)
            selected.append(payload)
            if len(selected) >= max_items:
                break
        return selected

    def reusable_tool_request_examples(
        self,
        property_name,
        form_text="",
        max_items=MEMORY_REUSE_MAX_ITEMS,
    ):
        prop = str(property_name or "").strip()
        if not prop:
            return []
        query_tokens = self._tokenize(form_text)
        candidates = []
        for entry in self.query_tool_request_library(property_name=prop, report_status="verified_pass", limit=max_items * 12 or 24):
            spec = entry.get("spec") or {}
            report = entry.get("report") or {}
            text_blob = " ".join(
                [
                    entry.get("form", ""),
                    entry.get("justification", ""),
                    str(spec.get("mode", "")),
                    str(report.get("summary", "")),
                ]
            )
            score = len(query_tokens & self._tokenize(text_blob))
            if entry.get("candidate_status") == "passed":
                score += 1
            candidates.append(
                (
                    score,
                    entry["candidate_id"],
                    {
                        "candidate_id": entry["candidate_id"],
                        "proposition_id": entry["proposition_id"],
                        "request_id": entry["request_id"],
                        "tool_name": entry.get("tool_name", "verification"),
                        "mode": str(spec.get("mode", "")).strip() or "[none]",
                        "justification": entry.get("justification", "") or "[none]",
                        "summary": str(report.get("summary", "")).strip() or "[none]",
                        "status": entry.get("report_status", "") or "[none]",
                    },
                )
            )
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected = []
        seen = set()
        for _, _, payload in candidates:
            signature = (payload["tool_name"], payload["mode"], payload["summary"])
            if signature in seen:
                continue
            seen.add(signature)
            selected.append(payload)
            if len(selected) >= max_items:
                break
        return selected

    def proposition_reuse_packet(
        self,
        property_name,
        form_text="",
        max_items=MEMORY_REUSE_MAX_ITEMS,
        max_chars=5000,
    ):
        examples = self.reusable_proposition_examples(
            property_name,
            form_text=form_text,
            max_items=max_items,
        )
        if not examples:
            return ""
        lines = []
        for item in examples:
            lines.extend(
                [
                    (
                        f"- {item['candidate_id']}/{item['proposition_id']}: "
                        f"signature={item['signature']}; title={item['title']}; "
                        f"tool={'yes' if item['requires_tool'] else 'no'}"
                    ),
                    f"  claim={item['claim']}",
                    f"  focus={item['verification_focus']}",
                    f"  derivation_hint={item['derivation_hint']}",
                    f"  conclusion={item['conclusion']}",
                ]
            )
        return self._truncate_text("\n".join(lines), max_chars=max_chars)

    def tool_request_reuse_packet(
        self,
        property_name,
        form_text="",
        max_items=MEMORY_REUSE_MAX_ITEMS,
        max_chars=4500,
    ):
        examples = self.reusable_tool_request_examples(
            property_name,
            form_text=form_text,
            max_items=max_items,
        )
        if not examples:
            return ""
        lines = []
        for item in examples:
            lines.extend(
                [
                    (
                        f"- {item['candidate_id']}/{item['proposition_id']}/{item['request_id']}: "
                        f"tool={item['tool_name']}; mode={item['mode']}; status={item['status']}"
                    ),
                    f"  justification={item['justification']}",
                    f"  summary={item['summary']}",
                ]
            )
        return self._truncate_text("\n".join(lines), max_chars=max_chars)

    def property_memory_packet(
        self,
        property_name,
        form_text="",
        max_items=MEMORY_PROPERTY_PACKET_MAX_ITEMS,
        max_chars=7000,
    ):
        prop = str(property_name or "").strip()
        if not prop:
            return ""
        parts = []
        dependency = PROPERTY_DEPENDENCIES.get(prop, "")
        if dependency:
            parts.append(f"性质依赖:\n- {prop}: {dependency}")

        similar = self.find_similar_failures(form_text, limit=max_items, property_name=prop)
        if similar:
            lines = []
            for record in similar:
                detail = record.property_status.get(prop) or {}
                lines.append(
                    f"- {record.candidate_id}: form={record.form}; status={detail.get('status', 'unknown')}; "
                    f"note={detail.get('note', '') or record.pruned_reason or '[无]'}"
                )
            parts.append("相似候选失败:\n" + "\n".join(lines))

        failed_props = self.proposition_history_summary(prop, status_filter={"fail"}, max_items=max_items)
        if failed_props:
            parts.append(f"历史 proposition 失败:\n{failed_props}")

        passed_props = self.proposition_history_summary(prop, status_filter={"pass"}, max_items=max_items)
        if passed_props:
            parts.append(f"历史 proposition 通过:\n{passed_props}")

        tool_history = self.tool_request_history_summary(prop, max_items=max_items)
        if tool_history:
            parts.append(f"历史 tool request:\n{tool_history}")

        reuse_packet = self.proposition_reuse_packet(prop, form_text=form_text, max_items=max_items, max_chars=max_chars)
        if reuse_packet:
            parts.append(f"可复用 proposition 模板:\n{reuse_packet}")

        tool_reuse = self.tool_request_reuse_packet(prop, form_text=form_text, max_items=max_items, max_chars=max_chars)
        if tool_reuse:
            parts.append(f"可复用 tool request 模板:\n{tool_reuse}")

        return self._truncate_text("\n\n".join(part for part in parts if part), max_chars=max_chars)

    def terminal_report_summary(self, max_items=MEMORY_TERMINAL_REPORT_MAX_ITEMS, max_chars=8000):
        parts = []
        for record in self.recent_terminal_candidates(limit=max_items):
            prop_bits = []
            for prop in DEFAULT_PROPERTIES:
                detail = record.property_status.get(prop) or {}
                status = str(detail.get("status", "")).strip() or "untested"
                if status != "untested":
                    prop_bits.append(f"{prop}={status}")
            parts.append(
                f"- {record.candidate_id}: status={record.status}; form={record.form}; "
                f"derived_from={record.derived_from or '[none]'}; "
                f"properties={', '.join(prop_bits) or '[none]'}; "
                f"pruned_reason={record.pruned_reason or '[none]'}; "
                f"decision={record.terminal_decision.get('action', '[none]') or '[none]'}"
            )
        return self._truncate_text("\n".join(parts).strip() or "暂无终态候选。", max_chars=max_chars)

    def derived_tree_summary(self, max_roots=6, max_chars=6000):
        tree = self.build_derived_tree()
        children = tree["children"]
        nodes = tree["nodes"]
        roots = sorted(
            [
                candidate_id
                for candidate_id, record in nodes.items()
                if not record.derived_from or record.derived_from not in nodes
            ]
        )
        lines = []
        for root in roots[: max(0, int(max_roots)) or len(roots)]:
            child_list = children.get(root) or []
            if child_list:
                lines.append(f"- {root} -> {', '.join(child_list[:6])}")
            else:
                lines.append(f"- {root}")
        return self._truncate_text("\n".join(lines).strip() or "暂无派生树。", max_chars=max_chars)

    def summarize_for_prompt(self, max_candidates=MEMORY_SUMMARIZE_MAX_CANDIDATES, max_chars=12000):
        latest = self.load_latest_candidates()
        latest.sort(key=lambda item: item.candidate_id)
        recent = latest[-max(0, int(max_candidates)) :] if max_candidates else latest
        failure_patterns = self.aggregate_failure_patterns()

        parts = []
        if failure_patterns:
            pattern_line = ", ".join(f"{name}:{count}" for name, count in sorted(failure_patterns.items()))
            parts.append(f"失败模式统计: {pattern_line}")
        dependency_text = self.dependency_knowledge_summary(max_items=len(DEFAULT_PROPERTIES))
        if dependency_text:
            parts.append(f"性质依赖知识:\n{dependency_text}")
        heuristics = self.heuristic_summary(max_items=6)
        if heuristics:
            parts.append(f"启发式:\n{heuristics}")
        tree_summary = self.derived_tree_summary(max_roots=6, max_chars=2000)
        if tree_summary:
            parts.append(f"派生树摘要:\n{tree_summary}")

        if not recent:
            parts.append("暂无历史候选。")
        else:
            for record in recent:
                status_bits = []
                for prop in DEFAULT_PROPERTIES:
                    detail = record.property_status.get(prop) or {}
                    status_bits.append(f"{prop}={detail.get('status', 'untested')}")
                summary = (
                    f"- {record.candidate_id}: status={record.status}; form={record.form}; "
                    f"derived_from={record.derived_from or '[none]'}; "
                    f"properties={'; '.join(status_bits)}"
                )
                proposition_bits = []
                for prop in DEFAULT_PROPERTIES:
                    snapshot = record.proposition_snapshot(prop, max_items=MEMORY_REUSE_MAX_ITEMS)
                    if snapshot:
                        proposition_bits.append(f"{prop}[{snapshot}]")
                if proposition_bits:
                    summary += f"; propositions={'; '.join(proposition_bits)}"
                if record.pruned_reason:
                    summary += f"; pruned_reason={record.pruned_reason}"
                if record.risk_notes:
                    summary += f"; risk={record.risk_notes}"
                if record.terminal_decision.get("action"):
                    summary += f"; decision={record.terminal_decision.get('action')}"
                parts.append(summary)

        return self._truncate_text("\n".join(parts), max_chars=max_chars)
