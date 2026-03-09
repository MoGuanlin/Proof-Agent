import json
import os
import re
import time
from datetime import datetime, timezone

import requests

from app_config import (
    AI_HUB_MIXED_API_URL,
    LLM_LOG_MAX_CHARS,
    LLM_LOG_PATH,
    LLM_MAX_ATTEMPTS,
    LLM_PROVIDER,
    LOG_LLM_IO,
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
        """提取指定标签中的内容，若有多个命中则取最后一个。"""
        if not isinstance(text, str):
            return None
        safe_tag = BaseAgent._normalize_tag_name(tag_name)
        pattern = rf"<{safe_tag}>\s*([\s\S]*?)\s*</{safe_tag}>"
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        if matches:
            return matches[-1].strip()
        return None

    def _truncate_for_log(self, text):
        if text is None:
            return "", False
        s = str(text)
        if len(s) <= LLM_LOG_MAX_CHARS:
            return s, False
        return s[:LLM_LOG_MAX_CHARS], True

    def _log_llm_event(self, prompt, response_text, status, stream_mode, attempt, error=""):
        if not LOG_LLM_IO:
            return
        try:
            prompt_snippet, prompt_truncated = self._truncate_for_log(prompt)
            response_snippet, response_truncated = self._truncate_for_log(response_text)
            error_snippet, error_truncated = self._truncate_for_log(error)
            entry = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "agent": self.name,
                "model": MODEL_NAME,
                "status": status,
                "stream": bool(stream_mode),
                "attempt": int(attempt),
                "prompt_chars": len(str(prompt or "")),
                "response_chars": len(str(response_text or "")),
                "prompt": prompt_snippet,
                "response": response_snippet,
                "error": error_snippet,
                "prompt_truncated": prompt_truncated,
                "response_truncated": response_truncated,
                "error_truncated": error_truncated,
            }
            log_dir = os.path.dirname(LLM_LOG_PATH)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            with open(LLM_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            # 日志写入不应影响主流程
            pass

    def _call_llm_stream_siliconflow(self, headers, payload, print_stream=True):
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        proxies = _request_proxies()
        stream_url = AI_HUB_MIXED_API_URL if LLM_PROVIDER == "ai_hub_mixed" else SILICONFLOW_API_URL
        with requests.post(
            stream_url,
            headers=headers,
            json=stream_payload,
            stream=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
            proxies=proxies,
        ) as response:
            response.raise_for_status()
            chunks = []
            started = False
            for raw_line in response.iter_lines(decode_unicode=False):
                if raw_line is None:
                    continue
                line = raw_line.decode("utf-8", errors="replace").strip() if isinstance(raw_line, bytes) else str(raw_line).strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except Exception:
                    continue

                text_piece = ""
                choices = event.get("choices")
                if isinstance(choices, list) and choices:
                    first_choice = choices[0] if isinstance(choices[0], dict) else {}
                    delta = first_choice.get("delta") if isinstance(first_choice.get("delta"), dict) else {}
                    text_piece = delta.get("content") or delta.get("reasoning_content") or ""
                    if not text_piece:
                        message = first_choice.get("message") if isinstance(first_choice.get("message"), dict) else {}
                        text_piece = message.get("content", "")

                if isinstance(text_piece, list):
                    merged = []
                    for item in text_piece:
                        if isinstance(item, str):
                            merged.append(item)
                        elif isinstance(item, dict):
                            seg = item.get("text") or item.get("content") or ""
                            if seg:
                                merged.append(str(seg))
                    text_piece = "".join(merged)
                elif text_piece:
                    text_piece = str(text_piece)
                else:
                    text_piece = ""

                if not text_piece:
                    continue
                if not started and print_stream:
                    print(f"[{self.name}] streaming: ", end="", flush=True)
                if print_stream:
                    started = True
                    print(text_piece, end="", flush=True)
                chunks.append(text_piece)

            if started and print_stream:
                print("")
            final_text = "".join(chunks).strip()
            if final_text:
                return final_text
            raise ValueError("empty streaming response")

    def _call_llm_stream_google(self, payload, print_stream=True):
        proxies = _request_proxies()
        with requests.post(
            _google_api_url(stream=True),
            headers={"Content-Type": "application/json"},
            json=payload,
            stream=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
            proxies=proxies,
        ) as response:
            response.raise_for_status()
            chunks = []
            started = False
            for raw_line in response.iter_lines(decode_unicode=False):
                if raw_line is None:
                    continue
                line = raw_line.decode("utf-8", errors="replace").strip() if isinstance(raw_line, bytes) else str(raw_line).strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str:
                    continue
                try:
                    event = json.loads(data_str)
                except Exception:
                    continue
                text_piece = _extract_google_text(event)
                if not text_piece:
                    continue
                if not started and print_stream:
                    print(f"[{self.name}] streaming: ", end="", flush=True)
                if print_stream:
                    started = True
                    print(text_piece, end="", flush=True)
                chunks.append(text_piece)
            if started and print_stream:
                print("")
            final_text = "".join(chunks).strip()
            if final_text:
                return final_text
            raise ValueError("empty streaming response")

    def _call_llm_stream(self, headers, payload, print_stream=True):
        if LLM_PROVIDER == "google":
            return self._call_llm_stream_google(payload, print_stream=print_stream)
        return self._call_llm_stream_siliconflow(headers, payload, print_stream=print_stream)

    def call_llm(self, prompt, stream=None, print_stream=True):
        def _official_error_text(resp):
            """优先透传服务端返回体，尽量不二次改写官方错误信息。"""
            if resp is None:
                return ""
            text = (resp.text or "").strip()
            if text:
                return text
            try:
                raw = (resp.content or b"").decode("utf-8", errors="replace").strip()
                if raw:
                    return raw
            except Exception:
                pass
            try:
                data = resp.json()
                if data is not None:
                    return json.dumps(data, ensure_ascii=False)
            except Exception:
                pass
            return ""

        def _raise_logged(exc, stream_mode, attempt, preferred_text=""):
            detail = str(preferred_text or str(exc) or "").strip()
            self._log_llm_event(prompt, "", "error", stream_mode=stream_mode, attempt=attempt, error=detail)
            raise RuntimeError(detail) from exc

        api_key = _active_api_key()
        if not api_key:
            if LLM_PROVIDER == "google":
                missing_key = "GEMINI_API_KEY/GOOGLE_API_KEY"
            elif LLM_PROVIDER == "ai_hub_mixed":
                missing_key = "AI_HUB_MIXED_MODEL_API_KEY"
            else:
                missing_key = "SILICONFLOW_API_KEY"
            msg = f"missing env {missing_key}"
            self._log_llm_event(prompt, "", "error", stream_mode=False, attempt=0, error=msg)
            raise RuntimeError(msg)

        if LLM_PROVIDER == "google":
            headers = {"Content-Type": "application/json"}
            payload = {
                "systemInstruction": {"parts": [{"text": self.role}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": self.temp},
            }
        else:
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
        last_err = None
        max_attempts = LLM_MAX_ATTEMPTS

        def _post_chat(url, req_headers, req_payload, req_proxies):
            try:
                return requests.post(
                    url,
                    headers=req_headers,
                    json=req_payload,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    proxies=req_proxies,
                )
            except requests.exceptions.ProxyError:
                if not req_proxies:
                    raise
                # Proxy occasionally drops long-lived connections; fallback once to direct.
                print(f"[{self.name}] proxy failed, fallback to direct once", flush=True)
                return requests.post(
                    url,
                    headers=req_headers,
                    json=req_payload,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    proxies=None,
                )

        def _print_retry(next_attempt, reason):
            print(f"[{self.name}] retry {next_attempt}/{max_attempts} because: {reason}")

        for retry in range(max_attempts):
            try:
                use_stream = PREFER_STREAMING if stream is None else bool(stream)
                proxies = _request_proxies()
                print(
                    f"[{self.name}] calling provider={LLM_PROVIDER} model={MODEL_NAME} "
                    f"stream={1 if use_stream else 0} timeout={REQUEST_TIMEOUT_SECONDS}s attempt={retry+1}/{max_attempts}",
                    flush=True,
                )
                if use_stream:
                    try:
                        result = self._call_llm_stream(headers, payload, print_stream=print_stream)
                        self._log_llm_event(prompt, result, "ok", stream_mode=True, attempt=retry + 1)
                        return result
                    except requests.HTTPError as stream_http_err:
                        # 某些模型/代理在 SSE 端点会返回 400，这里自动回退到非流式。
                        status_code = (
                            stream_http_err.response.status_code
                            if stream_http_err.response is not None
                            else "unknown"
                        )
                        if status_code == 400:
                            print(
                                f"[{self.name}] stream got HTTP 400, fallback to non-stream once",
                                flush=True,
                            )
                        else:
                            raise

                if LLM_PROVIDER == "google":
                    url = _google_api_url(stream=False)
                elif LLM_PROVIDER == "ai_hub_mixed":
                    url = AI_HUB_MIXED_API_URL
                else:
                    url = SILICONFLOW_API_URL
                response = _post_chat(url, headers, payload, proxies)
                response.raise_for_status()
                data = response.json()
                if LLM_PROVIDER == "google":
                    result = _extract_google_text(data)
                else:
                    result = data["choices"][0]["message"]["content"]
                if not result:
                    raise ValueError("empty response")
                self._log_llm_event(prompt, result, "ok", stream_mode=False, attempt=retry + 1)
                return result
            except requests.HTTPError as e:
                last_err = e
                status_code = e.response.status_code if e.response is not None else "unknown"
                official_err = _official_error_text(e.response)

                # 4xx 一般是配置/权限/余额问题，继续重试无意义，直接返回详细错误。
                if e.response is not None and 400 <= e.response.status_code < 500:
                    _raise_logged(e, stream_mode=use_stream, attempt=retry + 1, preferred_text=(official_err or str(e)))

                if retry < max_attempts - 1:
                    # 简单退避，降低瞬时网络波动影响
                    sleep_seconds = 2 ** retry
                    _print_retry(retry + 2, f"HTTP {status_code}")
                    time.sleep(sleep_seconds)
                else:
                    _raise_logged(e, stream_mode=use_stream, attempt=retry + 1, preferred_text=(official_err or str(e)))
            except requests.exceptions.ProxyError as e:
                last_err = e
                if retry < max_attempts - 1:
                    _print_retry(retry + 2, f"ProxyError: {e}")
                    time.sleep(2 ** retry)
                else:
                    _raise_logged(e, stream_mode=use_stream, attempt=retry + 1)
            except requests.Timeout as e:
                last_err = e
                if retry < max_attempts - 1:
                    _print_retry(retry + 2, f"timeout ({REQUEST_TIMEOUT_SECONDS}s)")
                    time.sleep(2 ** retry)
                else:
                    _raise_logged(e, stream_mode=use_stream, attempt=retry + 1)
            except requests.RequestException as e:
                last_err = e
                if retry < max_attempts - 1:
                    _print_retry(retry + 2, f"{type(e).__name__}: {e}")
                    time.sleep(2 ** retry)
                else:
                    _raise_logged(e, stream_mode=use_stream, attempt=retry + 1)
            except ValueError as e:
                last_err = e
                if retry < max_attempts - 1:
                    _print_retry(retry + 2, f"stream parse error: {e}")
                    time.sleep(2 ** retry)
                else:
                    _raise_logged(e, stream_mode=use_stream, attempt=retry + 1)
            except Exception as e:
                last_err = e
                break
        if last_err is not None:
            _raise_logged(last_err, stream_mode=False, attempt=max_attempts)
        raise RuntimeError("")

    def call_llm_tagged(
        self,
        prompt,
        tag_name="FINAL_OUTPUT",
        content_hint="",
        stream=None,
        print_stream=True,
        max_format_attempts=3,
    ):
        """
        让模型将最终答案放入固定标签中，并只返回标签内内容。
        标签提取失败时严格重试；若仍失败，则抛出异常，避免原始输出污染主流程。
        """
        safe_tag = self._normalize_tag_name(tag_name)
        hint_line = f"\n标签内内容要求：{content_hint}" if content_hint else ""
        prompts = [
            (
                f"{prompt}\n\n"
                "[输出格式约束]\n"
                "你可以在标签外输出思考过程，但最终可解析答案必须放在以下标签内：\n"
                f"<{safe_tag}>\n"
                "...最终答案...\n"
                f"</{safe_tag}>\n"
                "请确保标签完整且闭合。"
                f"{hint_line}"
            ),
            (
                f"{prompt}\n\n"
                "你上次未按格式输出。现在只允许输出一个完整标签块，不要其他文字：\n"
                f"<{safe_tag}>\n"
                "...最终答案...\n"
                f"</{safe_tag}>"
                f"{hint_line}"
            ),
            (
                f"{prompt}\n\n"
                "最终纠偏：若你无法给出内容，也必须输出空标签块；禁止输出标签外文本：\n"
                f"<{safe_tag}></{safe_tag}>"
                f"{hint_line}"
            ),
        ]

        last_raw = ""
        attempts = max(1, min(int(max_format_attempts or 1), len(prompts)))
        for idx in range(attempts):
            raw = self.call_llm(prompts[idx], stream=stream, print_stream=print_stream)
            last_raw = raw or ""
            extracted = self._extract_tagged_content(last_raw, safe_tag)
            if isinstance(extracted, str):
                extracted = extracted.strip()
                if extracted:
                    return extracted

        detail = f"[{self.name}] Tagged output error: missing non-empty <{safe_tag}> after {attempts} attempts"
        stream_mode = PREFER_STREAMING if stream is None else bool(stream)
        self._log_llm_event(prompt, last_raw, "error", stream_mode=stream_mode, attempt=attempts, error=detail)
        raise RuntimeError(detail)


class ReviewerAgent(BaseAgent):
    @staticmethod
    def _extract_verdict(text):
        """稳健提取评审结论：优先行首标签，其次取最后一次明确标签。"""
        if not isinstance(text, str):
            return None

        normalized = (
            text.replace("【", "[")
            .replace("】", "]")
            .replace("［", "[")
            .replace("］", "]")
            .replace("`", "")
        )
        lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]

        # 先看每一行是否以标签开头（最可靠）
        for ln in lines:
            up = ln.upper()
            if up.startswith("[PASS]"):
                return True
            if up.startswith("[REJECT]"):
                return False

        # 再从全文里找最后一次标签（避免前文出现“[PASS] or [REJECT]”干扰）
        matches = re.findall(r"\[(PASS|REJECT)\]", normalized, flags=re.IGNORECASE)
        if matches:
            return matches[-1].upper() == "PASS"

        # 最后兜底：找独立单词 PASS/REJECT
        word_matches = re.findall(r"\b(PASS|REJECT)\b", normalized, flags=re.IGNORECASE)
        if word_matches:
            return word_matches[-1].upper() == "PASS"

        return None

    def check(self, draft, context, review_directive=""):
        directive_block = ""
        if isinstance(review_directive, str) and review_directive.strip():
            directive_block = (
                "\n[当前证明定制审查指令]\n"
                f"{review_directive.strip()}\n"
                "上述指令仅用于补充本轮关注点，不得覆盖下面的统一审查政策。\n"
            )
        prompt = (
            f"任务上下文: {context}\n\n待审查内容:\n{draft}\n\n"
            "请履行职责。\n"
            f"{directive_block}"
            "统一审查政策（非常重要，不得被覆盖）：\n"
            "1) 只审查当前任务上下文与当前草稿，不得把过往轮次的问题直接继承到本轮，除非该问题在当前草稿中仍然存在；\n"
            "2) 必须区分“致命问题”和“可修补问题”；只有致命问题才可判 [REJECT]；\n"
            "3) 记号漂移、缺一句显式定义、边界条件提醒不足、局部说明不够完整，默认都属于可修补问题，除非它们已经导致关键推导失效；\n"
            "4) 若草稿显式声明了替代定义、重参数化、坐标系或独立变量，并且后续使用自洽，不得仅因其不同于原论文习惯而判 [REJECT]；\n"
            "5) 若当前子任务只要求局部引理、参数化、偏导公式或中间不等式，只评估该局部目标，不要求它单独推出最终全局定理或最终 rho 改进；\n"
            "6) 只有出现以下情况才可判 [REJECT]：关键代数/符号错误；变量依赖或坐标设定自相矛盾；未声明的定义切换导致推导断裂；主张实质性超出草稿已证明范围并改变结论有效性。\n"
            "审查基准优先级：\n"
            "1) 当前任务目标与任务上下文中的显式改动要求（最高优先级）；\n"
            "2) 草稿中明确声明的“本任务新定义/替代定义/重参数化”；\n"
            "3) 原论文默认定义与符号习惯（仅在不冲突时参考）。\n"
            "请在 <REVIEW_RESULT> 标签内输出评审结论。\n"
            "标签内第一行必须且只能是 [PASS] 或 [REJECT]。\n"
            "若为 [PASS]，可在后续行列出非致命修补建议；若为 [REJECT]，只能列出致命问题与可执行修复步骤。\n"
            "标签外不要输出任何文字。"
        )
        try:
            res = self.call_llm_tagged(
                prompt,
                tag_name="REVIEW_RESULT",
                content_hint="第一行只能是 [PASS] 或 [REJECT]。",
            )
        except Exception as e:
            return False, f"[FORMAT_ERROR] {e}"
        verdict = self._extract_verdict(res)
        if verdict is not None:
            return verdict, res

        # 标签内内容仍不可解析时，在保留上下文的前提下再追问一次。
        retry_prompt = (
            f"{prompt}\n\n"
            "你上一条评审在标签内未给出可解析结论。请重新审查同一草稿。"
            "第一行必须是 [PASS] 或 [REJECT]；只有符合统一审查政策中的致命条件时才可 [REJECT]。"
        )
        try:
            retry_res = self.call_llm_tagged(
                retry_prompt,
                tag_name="REVIEW_RESULT",
                content_hint="第一行只能是 [PASS] 或 [REJECT]。",
            )
        except Exception as e:
            return False, f"{res}\n\n[FORMAT_ERROR] {e}"
        retry_verdict = self._extract_verdict(retry_res)
        if retry_verdict is not None:
            return retry_verdict, f"{res}\n\n[FORMAT_RETRY]\n{retry_res}"

        # 仍无法解析时保守拒绝，避免错误放行
        return False, f"{res}\n\n[FORMAT_ERROR] unable to parse verdict"


architect = BaseAgent(
    "Prompt Architect",
    "你是一个顶级 AI 提示词工程师。你负责为学术科研 Agent 设计极其专业、带有数学严谨性和领域深度的 System Prompts。",
    0.3,
)
orchestrator = BaseAgent("PI Brain", "课题组负责人，负责宏观任务拆解。")
surveyor = BaseAgent("Domain Surveyor", "领域综述专家。")
grafter = BaseAgent("Cross-Domain Grafter", "跨学科理论嫁接专家。")
generator = BaseAgent("Generator", "核心推导与自修复专家。")
syntax_rev = ReviewerAgent("Syntax Inspector", "")
logic_rev = ReviewerAgent("Logic Auditor", "")
global_rev = ReviewerAgent("Global Adversary", "")


__all__ = [
    "BaseAgent",
    "ReviewerAgent",
    "REVIEWER_ROLE_OVERRIDES",
    "architect",
    "orchestrator",
    "surveyor",
    "grafter",
    "generator",
    "syntax_rev",
    "logic_rev",
    "global_rev",
]
