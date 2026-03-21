import json
import re

import requests

from app_config import (
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
        hint_line = f"\n标签内内容要求：{content_hint}" if content_hint else ""
        wrapped_prompt = (
            f"{prompt}\n\n"
            "[输出格式约束]\n"
            "你可以在标签外输出思考过程，但最终可解析答案必须放在以下标签内：\n"
            f"<{safe_tag}>\n"
            "...最终答案...\n"
            f"</{safe_tag}>\n"
            "请确保标签完整且闭合。"
            f"{hint_line}"
        )
        raw = self.call_llm(wrapped_prompt, stream=stream, print_stream=print_stream)
        extracted = self._extract_tagged_content(raw, safe_tag)
        if not extracted:
            raise RuntimeError(f"[{self.name}] missing non-empty <{safe_tag}>")
        return extracted


class ReviewerAgent(BaseAgent):
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
            "6) 若任务上下文或 verification 并未要求完成最终全局定理，而草稿却声称“已经证明最终全局定理”“最终上界已成立”“对所有构型已完成全局证明”“完整证明闭环已完成”等，这属于实质性 overclaim，可直接判 [REJECT]；\n"
            "7) 只有出现以下情况才可判 [REJECT]：关键代数/符号错误；变量依赖或坐标设定自相矛盾；未声明的定义切换导致推导断裂；主张实质性超出草稿已证明范围并改变结论有效性。\n"
            "审查基准优先级：\n"
            "1) 当前任务目标与任务上下文中的显式改动要求（最高优先级）；\n"
            "2) 草稿中明确声明的“本任务新定义/替代定义/重参数化”；\n"
            "3) 原论文默认定义与符号习惯（仅在不冲突时参考）。\n"
            "请在 <REVIEW_RESULT> 标签内输出评审结论。\n"
            "标签内第一行必须且只能是 [PASS] 或 [REJECT]。\n"
            "若为 [PASS]，可在后续行列出非致命修补建议；若为 [REJECT]，只能列出致命问题与可执行修复步骤。\n"
        )
        result = self.call_llm_tagged(
            prompt,
            tag_name="REVIEW_RESULT",
            content_hint="第一行只能是 [PASS] 或 [REJECT]。",
        )
        verdict = self._extract_verdict(result)
        if verdict is None:
            raise RuntimeError(f"[{self.name}] unable to parse reviewer verdict")
        return verdict, result

orchestrator = BaseAgent("PI Brain", "课题组负责人，负责宏观任务拆解。")
logic_rev = ReviewerAgent("Logic Auditor", "")
global_rev = ReviewerAgent("Global Adversary", "")
potential_designer = BaseAgent(
    "Potential Function Designer",
    (
        "你负责为 Delaunay triangulation stretch factor 上界改进任务提出新的势函数候选。"
        "输出必须聚焦候选形式、设计动机、与 N1/N2/N3/D4/Q5/Q6 的关系，以及与历史失败案例的差异。"
        "不要写泛泛研究计划；优先给出可被后续证明规划直接消费的精确数学对象。"
    ),
    0.8,
)
proof_planner = BaseAgent(
    "Proof Strategy Planner",
    (
        "你负责对给定势函数候选制定证明路线图。"
        "必须明确哪些命题可复用、哪些必须重写、优先验证顺序、主要风险，以及是否存在能直接剪枝的显然失败点。"
        "优先检查 N2/N3 这类必要条件，再处理 D4/Q5/Q6。"
    ),
    0.4,
)
proof_writer = BaseAgent(
    "Proof Writer",
    (
        "你负责针对单个候选和单个性质撰写严格的数学证明草稿。"
        "必须明确假设、推导链、边界条件、仍需数值/符号验证的部分，以及当前性质究竟证明到了什么强度。"
        "禁止把局部性质夸大成全局上界改进已经完成。"
    ),
    0.5,
)
correctness_checker = ReviewerAgent(
    "Correctness Checker",
    (
        "你是候选势函数证明的正确性检查员。"
        "当前主流程里，你以 proposition 粒度审查单个证明草稿。"
        "只在发现真正致命的数学错误、断裂或越界结论时拒绝。"
        "若问题可修补，则通过并给出具体修补建议。"
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
