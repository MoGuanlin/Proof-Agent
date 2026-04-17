import json
import re

import requests

from .app_config import (
    AI_HUB_MIXED_API_URL,
    LLM_PROVIDER,
    MODEL_NAME,
    PREFER_STREAMING,
    REQUEST_TIMEOUT_SECONDS,
    REVIEWER_ROLE_OVERRIDES,
    SILICONFLOW_API_URL,
    _active_api_key,
    _extract_google_text,
    _google_api_url,
    _request_proxies,
)


class BaseAgent:
    def __init__(self, name, system_role="", temperature=0.7):
        self.name = name
        self.role = system_role
        self.temp = temperature

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

    def _headers_and_payload(self, prompt):
        api_key = _active_api_key()
        if not api_key:
            if LLM_PROVIDER == "google":
                missing_key = "GEMINI_API_KEY/GOOGLE_API_KEY"
            elif LLM_PROVIDER == "ai_hub_mixed":
                missing_key = "AI_HUB_MIXED_MODEL_API_KEY"
            else:
                missing_key = "SILICONFLOW_API_KEY"
            raise RuntimeError(f"missing env {missing_key}")

        if LLM_PROVIDER == "google":
            headers = {"Content-Type": "application/json"}
            payload = {
                "systemInstruction": {"parts": [{"text": self.role}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": self.temp},
            }
            return headers, payload

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": self.role},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temp,
        }
        return headers, payload

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

    def _stream_openai_compatible(self, headers, payload, print_stream):
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        url = AI_HUB_MIXED_API_URL if LLM_PROVIDER == "ai_hub_mixed" else SILICONFLOW_API_URL
        response = requests.post(
            url,
            headers=headers,
            json=stream_payload,
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
            data = line[5:].strip()
            if data == "[DONE]":
                break
            event = json.loads(data)
            choice = event["choices"][0]
            delta = choice.get("delta") or {}
            text = delta.get("content") or delta.get("reasoning_content")
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
        if not result:
            raise RuntimeError("empty streaming response")
        return result

    def _call_stream(self, headers, payload, print_stream):
        if LLM_PROVIDER == "google":
            return self._stream_google(payload, print_stream)
        return self._stream_openai_compatible(headers, payload, print_stream)

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
            url = AI_HUB_MIXED_API_URL if LLM_PROVIDER == "ai_hub_mixed" else SILICONFLOW_API_URL
            response = requests.post(
                url,
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
        raw = self.call_llm(wrapped_prompt, stream=stream, print_stream=print_stream)
        extracted = self._extract_tagged_content(raw, safe_tag)
        if extracted:
            return extracted

        extracted = self._extract_json_fallback(raw, content_hint=content_hint)
        if extracted:
            print(
                f"[{self.name}] recovered missing <{safe_tag}> via JSON fallback",
                flush=True,
            )
            return extracted

        if safe_tag.upper() == "REVIEW_RESULT":
            verdict_text = ReviewerAgent._extract_fulltext_reviewer_result(raw)
            if verdict_text:
                print(
                    f"[{self.name}] recovered missing <{safe_tag}> via full-text verdict fallback",
                    flush=True,
                )
                return verdict_text

        repair_prompt = (
            f"Your previous reply did not provide parseable content inside <{safe_tag}>.\n"
            f"Please output exactly one complete and closed <{safe_tag}> tag, with no content outside the tag.\n"
            f"<{safe_tag}>\n"
            "...content...\n"
            f"</{safe_tag}>\n"
            f"{hint_line}\n"
            "If this is a review result, the first line inside the tag must be exactly [PASS] or [REJECT]."
        )
        repair_raw = self.call_llm(repair_prompt, stream=stream, print_stream=print_stream)
        extracted = self._extract_tagged_content(repair_raw, safe_tag)
        if extracted:
            print(
                f"[{self.name}] recovered missing <{safe_tag}> via format-repair retry",
                flush=True,
            )
            return extracted

        extracted = self._extract_json_fallback(repair_raw, content_hint=content_hint)
        if extracted:
            print(
                f"[{self.name}] recovered missing <{safe_tag}> via JSON fallback after retry",
                flush=True,
            )
            return extracted

        if safe_tag.upper() == "REVIEW_RESULT":
            verdict_text = ReviewerAgent._extract_fulltext_reviewer_result(repair_raw)
            if verdict_text:
                print(
                    f"[{self.name}] recovered missing <{safe_tag}> via full-text verdict fallback after retry",
                    flush=True,
                )
                return verdict_text

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
