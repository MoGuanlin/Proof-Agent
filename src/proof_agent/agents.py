import json
import re

import requests

from .app_config import (
    LLM_PROVIDER,
    MODEL_NAME,
    MODEL_HIDE_REASONING_OUTPUT,
    MODEL_REASONING_EFFORT,
    PREFER_STREAMING,
    REQUEST_TIMEOUT_SECONDS,
    _active_api_key,
    _extract_google_text,
    _google_api_url,
    _openai_compatible_api_url,
    _openai_compatible_extra_headers,
    _request_proxies,
)
from .retry import IncompleteStreamError, with_http_retry


class BaseAgent:
    def __init__(self, name, system_role="", temperature=0.7, attached_file_uris=None,
                 literature_packet=None):
        self.name = name
        self.role = system_role
        self.temp = temperature
        self.attached_file_uris = list(attached_file_uris or [])
        # Text literature packet prepended to the user prompt for OpenAI-compatible
        # providers (google uses attached_file_uris instead). Empty => no injection.
        self.literature_packet = str(literature_packet or "").strip()

    @staticmethod
    def _normalize_tag_name(tag_name):
        if not isinstance(tag_name, str):
            return "FINAL_OUTPUT"
        cleaned = re.sub(r"[^A-Za-z0-9_:-]+", "", tag_name.strip())
        return cleaned or "FINAL_OUTPUT"

    @staticmethod
    def _extract_tagged_content(text, tag_name):
        if not isinstance(text, str):
            return None
        safe_tag = BaseAgent._normalize_tag_name(tag_name)
        matches = re.findall(
            rf"<{safe_tag}>\s*([\s\S]*?)\s*</{safe_tag}>",
            text,
            flags=re.IGNORECASE,
        )
        return matches[-1].strip() if matches else None

    @staticmethod
    def _wrap_tagged_content(text, tag_name):
        safe_tag = BaseAgent._normalize_tag_name(tag_name)
        body = str(text or "").strip()
        if not body:
            return ""
        return f"<{safe_tag}>\n{body}\n</{safe_tag}>"

    @staticmethod
    def _expects_json_content(content_hint):
        hint = str(content_hint or "").lower()
        return "json" in hint or "object" in hint or "array" in hint

    @staticmethod
    def _coerce_json_candidate(candidate, content_hint=""):
        text = str(candidate or "").strip()
        if not text:
            return None
        hint = str(content_hint or "").lower()
        wants_array = "array" in hint
        wants_object = "object" in hint
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if wants_array and not isinstance(parsed, list):
            return None
        if wants_object and not isinstance(parsed, dict):
            return None
        return text

    @staticmethod
    def _extract_last_json_substring(text, content_hint=""):
        source = str(text or "")
        if not source.strip():
            return None
        hint = str(content_hint or "").lower()
        wants_array = "array" in hint
        wants_object = "object" in hint
        decoder = json.JSONDecoder()
        openings = []
        for idx, ch in enumerate(source):
            if ch == "{":
                openings.append((idx, "{"))
            elif ch == "[":
                openings.append((idx, "["))
        for idx, ch in reversed(openings):
            if wants_object and ch != "{":
                continue
            if wants_array and ch != "[":
                continue
            try:
                _, end = decoder.raw_decode(source[idx:])
            except json.JSONDecodeError:
                continue
            candidate = source[idx : idx + end].strip()
            validated = BaseAgent._coerce_json_candidate(candidate, content_hint=content_hint)
            if validated:
                return validated
        return None

    @staticmethod
    def _extract_json_fallback(text, content_hint=""):
        if not BaseAgent._expects_json_content(content_hint):
            return None
        source = str(text or "")
        fenced_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", source, flags=re.IGNORECASE)
        for block in reversed(fenced_blocks):
            validated = BaseAgent._coerce_json_candidate(block, content_hint=content_hint)
            if validated:
                return validated
        direct = BaseAgent._coerce_json_candidate(source.strip(), content_hint=content_hint)
        if direct:
            return direct
        return BaseAgent._extract_last_json_substring(source, content_hint=content_hint)

    @staticmethod
    def _merge_openai_content(piece):
        if isinstance(piece, str):
            return piece
        if not isinstance(piece, list):
            return ""
        chunks = []
        for item in piece:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    chunks.append(str(text))
        return "".join(chunks)

    @staticmethod
    def _extract_partial_tagged_content(text, tag_name):
        if not isinstance(text, str):
            return None
        safe_tag = BaseAgent._normalize_tag_name(tag_name)
        opening_matches = list(
            re.finditer(
                rf"<{safe_tag}>\s*",
                text,
                flags=re.IGNORECASE,
            )
        )
        if not opening_matches:
            return None
        start = opening_matches[-1].end()
        closing_match = re.search(
            rf"</{safe_tag}>",
            text[start:],
            flags=re.IGNORECASE,
        )
        end = start + closing_match.start() if closing_match else len(text)
        candidate = text[start:end].strip()
        return candidate or None

    @staticmethod
    def _build_tag_repair_prompt(prompt, safe_tag, content_hint, recovered_content):
        hint_line = f"\nContent requirements inside the tag: {content_hint}" if content_hint else ""
        recovered_body = str(recovered_content or "").strip() or "...content..."
        recovered_block = f"<{safe_tag}>\n{recovered_body}\n</{safe_tag}>"
        if safe_tag.upper() == "PROPOSITION_PROOF":
            return (
                f"Original task:\n{prompt}\n\n"
                f"Your previous reply for <{safe_tag}> was truncated, malformed, or semantically invalid.\n"
                "Below is the partial proposition proof recovered from the failed attempt.\n"
                "Continue and repair this exact proof draft instead of regenerating from scratch.\n"
                "Do not switch to reviewer mode. Do not output [PASS] or [REJECT].\n"
                "Preserve the fixed six-section skeleton exactly:\n"
                "## Assumptions\n"
                "## Claim\n"
                "## Derivation\n"
                "## Boundary Cases\n"
                "## Verification Needs\n"
                "## Conclusion\n"
                "Keep any still-correct existing proof text, complete cut-off formulas or sentences, and return the full revised proof only.\n\n"
                f"Recovered partial content:\n{recovered_block}\n\n"
                f"Please output exactly one complete and closed <{safe_tag}> tag, with no content outside the tag."
                f"{hint_line}"
            )
        return (
            f"Original task:\n{prompt}\n\n"
            f"Your previous reply did not provide parseable content inside <{safe_tag}>.\n"
            "Below is the recovered partial content from the failed attempt. Repair it into one complete tagged answer.\n"
            f"Recovered partial content:\n{recovered_block}\n\n"
            f"Please output exactly one complete and closed <{safe_tag}> tag, with no content outside the tag."
            f"{hint_line}\n"
            "If this is a review result, the first line inside the tag must be exactly [PASS] or [REJECT]."
        )

    @staticmethod
    def _reasoning_effort_value():
        effort = str(MODEL_REASONING_EFFORT or "").strip()
        return effort or None

    @staticmethod
    def _stream_delta_text(choice):
        delta = (choice or {}).get("delta") or {}
        text = delta.get("content")
        if text is not None:
            return text
        if MODEL_HIDE_REASONING_OUTPUT:
            return ""
        return delta.get("reasoning_content") or ""

    def _headers_and_payload(self, prompt):
        api_key = _active_api_key()
        if not api_key:
            if LLM_PROVIDER == "google":
                missing_key = "GEMINI_API_KEY/GOOGLE_API_KEY"
            elif LLM_PROVIDER == "ai_hub_mixed":
                missing_key = "AI_HUB_MIXED_MODEL_API_KEY"
            elif LLM_PROVIDER == "openai":
                missing_key = "OPENAI_API_KEY"
            elif LLM_PROVIDER == "openrouter":
                missing_key = "OPENROUTER_API_KEY"
            else:
                missing_key = "SILICONFLOW_API_KEY"
            raise RuntimeError(f"missing env {missing_key}")

        if LLM_PROVIDER == "google":
            user_parts = []
            for uri, mime in self.attached_file_uris:
                user_parts.append({"fileData": {"fileUri": uri, "mimeType": mime or "application/pdf"}})
            user_parts.append({"text": prompt})
            headers = {"Content-Type": "application/json"}
            payload = {
                "systemInstruction": {"parts": [{"text": self.role}]},
                "contents": [{"role": "user", "parts": user_parts}],
                "generationConfig": {"temperature": self.temp},
            }
            return headers, payload

        if self.attached_file_uris:
            raise NotImplementedError(
                f"attached_file_uris is google-only in Phase 1; current LLM_PROVIDER={LLM_PROVIDER!r}"
            )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        headers.update(_openai_compatible_extra_headers())
        user_content = prompt
        if self.literature_packet:
            user_content = (
                "Reference literature (retrieved from local RAG; relevant excerpts only, "
                "do not assume completeness):\n"
                f"{self.literature_packet}\n\n"
                "----- end reference literature -----\n\n"
                f"{prompt}"
            )
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": self.role},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temp,
        }
        if LLM_PROVIDER == "openai":
            effort = self._reasoning_effort_value()
            if effort:
                payload["reasoning_effort"] = effort
        return headers, payload

    @with_http_retry("stream_google")
    def _stream_google(self, payload, print_stream):
        response = requests.post(
            _google_api_url(stream=True),
            headers={"Content-Type": "application/json"},
            json=payload,
            stream=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
            proxies=_request_proxies(),
        )
        response.raise_for_status()
        pieces = []
        started = False
        for raw_line in response.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = (
                raw_line.decode("utf-8", errors="replace").strip()
                if isinstance(raw_line, bytes)
                else str(raw_line).strip()
            )
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:].strip())
            text = _extract_google_text(event)
            if not text:
                continue
            if print_stream and not started:
                print(f"[{self.name}] streaming: ", end="", flush=True)
                started = True
            if print_stream:
                print(text, end="", flush=True)
            pieces.append(text)
        if print_stream and started:
            print("")
        result = "".join(pieces).strip()
        if not result:
            raise RuntimeError("empty streaming response")
        return result

    @with_http_retry("stream_openai")
    def _stream_openai_compatible(self, headers, payload, print_stream):
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        response = requests.post(
            _openai_compatible_api_url(),
            headers=headers,
            json=stream_payload,
            stream=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
            proxies=_request_proxies(),
        )
        response.raise_for_status()
        pieces = []
        started = False
        saw_done = False
        for raw_line in response.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = (
                raw_line.decode("utf-8", errors="replace").strip()
                if isinstance(raw_line, bytes)
                else str(raw_line).strip()
            )
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                saw_done = True
                break
            event = json.loads(data)
            choice = event["choices"][0]
            text = self._stream_delta_text(choice)
            if not text:
                text = (choice.get("message") or {}).get("content", "")
            text = self._merge_openai_content(text)
            if not text:
                continue
            if print_stream and not started:
                print(f"[{self.name}] streaming: ", end="", flush=True)
                started = True
            if print_stream:
                print(text, end="", flush=True)
            pieces.append(text)
        if print_stream and started:
            print("")
        result = "".join(pieces).strip()
        if not saw_done:
            raise IncompleteStreamError(
                f"incomplete streaming response without [DONE] (received_chars={len(result)})",
                partial_text=result,
            )
        if not result:
            raise RuntimeError("empty streaming response")
        return result

    def _call_stream(self, headers, payload, print_stream):
        if LLM_PROVIDER == "google":
            return self._stream_google(payload, print_stream)
        return self._stream_openai_compatible(headers, payload, print_stream)

    @with_http_retry("non_stream_llm")
    def _call_non_stream(self, headers, payload):
        if LLM_PROVIDER == "google":
            response = requests.post(
                _google_api_url(stream=False),
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
                proxies=_request_proxies(),
            )
            response.raise_for_status()
            result = _extract_google_text(response.json())
        else:
            response = requests.post(
                _openai_compatible_api_url(),
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
                proxies=_request_proxies(),
            )
            response.raise_for_status()
            result = response.json()["choices"][0]["message"]["content"]
            result = self._merge_openai_content(result)
        if not result:
            raise RuntimeError("empty response")
        return result

    def call_llm(self, prompt, stream=None, print_stream=True):
        headers, payload = self._headers_and_payload(prompt)
        use_stream = PREFER_STREAMING if stream is None else bool(stream)
        print(
            f"[{self.name}] calling provider={LLM_PROVIDER} model={MODEL_NAME} "
            f"stream={1 if use_stream else 0} timeout={REQUEST_TIMEOUT_SECONDS}s",
            flush=True,
        )
        if use_stream:
            return self._call_stream(headers, payload, print_stream)
        return self._call_non_stream(headers, payload)

    def call_llm_tagged(
        self,
        prompt,
        tag_name="FINAL_OUTPUT",
        content_hint="",
        stream=None,
        print_stream=True,
        max_format_attempts=3,
    ):
        del max_format_attempts
        safe_tag = self._normalize_tag_name(tag_name)
        hint_line = f"\nContent requirements inside the tag: {content_hint}" if content_hint else ""
        wrapped_prompt = (
            f"{prompt}\n\n"
            "[Output Format Constraint]\n"
            "You may think outside the tag, but the final machine-parseable answer must appear inside the following tag:\n"
            f"<{safe_tag}>\n"
            "...final answer...\n"
            f"</{safe_tag}>\n"
            "Make sure the tag is complete and properly closed."
            f"{hint_line}"
        )
        invalid_reasons = []
        raw = ""
        repair_seed = ""
        initial_stream_complete = True

        def _maybe_accept(candidate_text, stage_label, allow_return=True, success_message=""):
            if not candidate_text:
                return None
            if not allow_return:
                invalid_reasons.append(f"{stage_label}: stream ended before completion marker")
                print(
                    f"[{self.name}] rejected <{safe_tag}> {stage_label}: incomplete stream",
                    flush=True,
                )
                return None
            if success_message:
                print(success_message, flush=True)
            return candidate_text

        try:
            raw = self.call_llm(wrapped_prompt, stream=stream, print_stream=print_stream)
        except IncompleteStreamError as exc:
            raw = str(exc.partial_text or "")
            repair_seed = self._extract_partial_tagged_content(raw, safe_tag) or raw.strip()
            initial_stream_complete = False
            invalid_reasons.append(f"initial stream incomplete: {exc}")
            print(
                f"[{self.name}] incomplete stream for <{safe_tag}>; attempting repair",
                flush=True,
            )

        if initial_stream_complete:
            extracted = self._extract_tagged_content(raw, safe_tag)
            accepted = _maybe_accept(extracted, "initial tagged content")
            if accepted:
                return accepted
            if extracted:
                repair_seed = extracted

            extracted = self._extract_json_fallback(raw, content_hint=content_hint)
            accepted = _maybe_accept(
                extracted,
                "initial JSON fallback",
                success_message=f"[{self.name}] recovered missing <{safe_tag}> via JSON fallback",
            )
            if accepted:
                return accepted
            if extracted and not repair_seed:
                repair_seed = extracted

            if safe_tag.upper() == "REVIEW_RESULT":
                verdict_text = ReviewerAgent._extract_fulltext_reviewer_result(raw)
                if verdict_text:
                    print(
                        f"[{self.name}] recovered missing <{safe_tag}> via full-text verdict fallback",
                        flush=True,
                    )
                    return verdict_text

        if not repair_seed:
            repair_seed = self._extract_partial_tagged_content(raw, safe_tag) or str(raw or "").strip()

        repair_prompt = self._build_tag_repair_prompt(prompt, safe_tag, content_hint, repair_seed)
        repair_raw = ""
        repair_stream_complete = True
        try:
            repair_raw = self.call_llm(repair_prompt, stream=stream, print_stream=print_stream)
        except IncompleteStreamError as exc:
            repair_raw = str(exc.partial_text or "")
            repair_stream_complete = False
            invalid_reasons.append(f"repair stream incomplete: {exc}")
            print(
                f"[{self.name}] incomplete repair stream for <{safe_tag}>",
                flush=True,
            )

        extracted = self._extract_tagged_content(repair_raw, safe_tag)
        accepted = _maybe_accept(
            extracted,
            "format-repair retry",
            allow_return=repair_stream_complete,
            success_message=f"[{self.name}] recovered missing <{safe_tag}> via format-repair retry",
        )
        if accepted:
            return accepted

        extracted = self._extract_json_fallback(repair_raw, content_hint=content_hint)
        accepted = _maybe_accept(
            extracted,
            "JSON fallback after retry",
            allow_return=repair_stream_complete,
            success_message=f"[{self.name}] recovered missing <{safe_tag}> via JSON fallback after retry",
        )
        if accepted:
            return accepted

        if safe_tag.upper() == "REVIEW_RESULT":
            verdict_text = ReviewerAgent._extract_fulltext_reviewer_result(repair_raw)
            if verdict_text:
                print(
                    f"[{self.name}] recovered missing <{safe_tag}> via full-text verdict fallback after retry",
                    flush=True,
                )
                return verdict_text

        if invalid_reasons:
            raise RuntimeError(f"[{self.name}] invalid <{safe_tag}> after repair: {invalid_reasons[-1]}")
        raise RuntimeError(f"[{self.name}] missing non-empty <{safe_tag}>")


class ReviewerAgent(BaseAgent):
    @staticmethod
    def _extract_fulltext_reviewer_result(text):
        source = str(text or "").strip()
        if not source:
            return None
        source = re.sub(r"(?is)</?REVIEW_RESULT\s*>", "", source).strip()
        match = re.search(r"(?im)^\s*(\[(?:PASS|REJECT)\])\s*$", source)
        if match:
            verdict = match.group(1).upper()
            tail = source[match.end() :].strip()
            return f"{verdict}\n{tail}".strip()
        inline = re.search(r"(?i)\[(pass|reject)\]", source)
        if inline:
            verdict = f"[{inline.group(1).upper()}]"
            tail = source[inline.end() :].strip()
            return f"{verdict}\n{tail}".strip()
        return None

    @staticmethod
    def _contains_fatal_issue_language(text):
        normalized = str(text or "").lower()
        fatal_markers = (
            "fatal issue",
            "fatal issues",
            "fatal flaw",
            "fatal flaws",
            "fatal error",
        )
        return any(marker in normalized for marker in fatal_markers)

    @staticmethod
    def _force_reject_format(text):
        normalized = str(text or "").strip()
        if not normalized:
            return "[REJECT]\nA fatal issue triggered a hard rejection, but the reviewer did not return parseable content."
        if re.match(r"(?im)^\s*\[pass\]\s*$", normalized):
            return re.sub(r"(?im)^\s*\[pass\]\s*$", "[REJECT]", normalized, count=1)
        lines = normalized.splitlines()
        if lines:
            first = lines[0].strip()
            if re.match(r"(?i)^\[(pass|reject)\]$", first):
                lines[0] = "[REJECT]"
                return "\n".join(lines)
        return f"[REJECT]\n{normalized}"

    @staticmethod
    def _extract_verdict(text):
        if not isinstance(text, str):
            return None
        normalized = (
            text.replace("【", "[")
            .replace("】", "]")
            .replace("［", "[")
            .replace("］", "]")
            .replace("`", "")
        )
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        for line in lines:
            upper = line.upper()
            if upper.startswith("[PASS]"):
                return True
            if upper.startswith("[REJECT]"):
                return False
        matches = re.findall(r"\[(PASS|REJECT)\]", normalized, flags=re.IGNORECASE)
        if not matches:
            return None
        return matches[-1].upper() == "PASS"

    def check(self, draft, context, review_directive=""):
        directive_block = ""
        if isinstance(review_directive, str) and review_directive.strip():
            directive_block = (
                "\n[Current Proof-Specific Review Directive]\n"
                f"{review_directive.strip()}\n"
                "This directive only adds task-local focus for the current round and must not override the unified review policy below.\n"
            )
        prompt = (
            f"Task context: {context}\n\nDraft under review:\n{draft}\n\n"
            "Perform your reviewer role.\n"
            f"{directive_block}"
            "Unified review policy (very important; do not override):\n"
            "1) Review only the current task context and the current draft. Do not import objections from prior rounds unless they are still literally true in the current draft.\n"
            "2) Distinguish fatal issues from fixable issues. Only fatal issues justify [REJECT].\n"
            "3) Minor notation drift, one missing explicit definition, incomplete boundary reminders, or locally thin exposition are fixable by default unless they invalidate a key derivation.\n"
            "4) If the draft explicitly declares replacement definitions, reparameterizations, coordinates, or independent variables and then uses them consistently, do not reject merely because they differ from the original paper's conventions.\n"
            "5) If the current subtask is only a local lemma, parameterization, derivative identity, or intermediate inequality, evaluate only that local target and do not require it to establish the final global theorem or final rho improvement by itself.\n"
            "6) If the task context or verification contract does not require the final global theorem, but the draft claims that the final global theorem, final upper bound, global proof for all configurations, or a complete proof closure has already been established, that is a material overclaim and may be rejected directly.\n"
            "7) Reject only for concrete algebraic or symbolic mistakes, contradictions in variable dependence or coordinate setup, undeclared definition switches that break the derivation, or claims materially stronger than the draft supports.\n"
            "Review priority:\n"
            "1) Explicit task-local requirements and modifications in the current task context.\n"
            "2) Any replacement definitions or reparameterizations explicitly declared in the current draft.\n"
            "3) The original paper's default definitions and notation, only as a fallback when there is no conflict.\n"
            "Output your review only inside <REVIEW_RESULT>.\n"
            "The first line inside the tag must be exactly [PASS] or [REJECT].\n"
            "If you use phrases like 'fatal issue', 'fatal flaw', or equivalent language later in the review, the first line must be [REJECT]. If the first line is [PASS], all later comments must be non-fatal repair suggestions.\n"
            "If you return [PASS], you may list non-fatal fixes afterward. If you return [REJECT], list only the fatal issues and concrete repair steps.\n"
        )
        result = self.call_llm_tagged(
            prompt,
            tag_name="REVIEW_RESULT",
            content_hint="The first line must be exactly [PASS] or [REJECT].",
        )
        verdict = self._extract_verdict(result)
        if verdict is None:
            raise RuntimeError(f"[{self.name}] unable to parse reviewer verdict")
        if verdict and self._contains_fatal_issue_language(result):
            result = self._force_reject_format(result)
            verdict = False
        return verdict, result

orchestrator = BaseAgent("PI Brain", "You are the principal investigator. Drive high-level decomposition and strategic research decisions.")
logic_rev = ReviewerAgent("Logic Auditor", "")
global_rev = ReviewerAgent("Global Adversary", "")
potential_designer = BaseAgent(
    "Potential Function Designer",
    (
        "You propose new potential-function candidates for improving the upper bound on the Delaunay triangulation stretch factor. "
        "Focus on the candidate's exact mathematical form, design motivation, relation to N1/N2/N3/D4/Q5/Q6, and how it differs from historical failures. "
        "Do not write generic research plans; prioritize precise mathematical objects that downstream proof planning can consume directly."
    ),
    0.8,
)
proof_planner = BaseAgent(
    "Proof Strategy Planner",
    (
        "You design the proof roadmap for a given potential-function candidate. "
        "Make explicit which propositions can be reused, which must be redone, the preferred verification order, the main risks, and whether there is an obvious early failure that justifies pruning. "
        "Prioritize necessary conditions such as N2/N3 before D4/Q5/Q6."
    ),
    0.4,
)
proof_writer = BaseAgent(
    "Proof Writer",
    (
        "You write rigorous mathematical proof drafts for one candidate and one property at a time. "
        "State assumptions, the derivation chain, boundary cases, any remaining numeric or symbolic verification needs, and the exact strength of what has been established. "
        "Do not inflate a local result into a completed global upper-bound improvement."
    ),
    0.5,
)
correctness_checker = ReviewerAgent(
    "Correctness Checker",
    (
        "You are the correctness checker for candidate potential-function proofs. "
        "In the current pipeline you review one proof draft at proposition granularity. "
        "Reject only for truly fatal mathematical errors, broken reasoning, or overreaching conclusions. "
        "If the issue is repairable, pass and give concrete repair suggestions."
    ),
)


__all__ = [
    "BaseAgent",
    "ReviewerAgent",
    "REVIEWER_ROLE_OVERRIDES",
    "orchestrator",
    "logic_rev",
    "global_rev",
    "potential_designer",
    "proof_planner",
    "proof_writer",
    "correctness_checker",
]
