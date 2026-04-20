#!/usr/bin/env python3
import argparse
import json
import os
import sys
from urllib.parse import quote

import requests

from proof_agent.paths import ENV_FILE, resolve_from_project


SILICONFLOW_API_URL = "https://api.siliconflow.cn/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


def openai_base_url():
    return (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").strip().rstrip("/")


def load_dotenv_file(path=ENV_FILE):
    dotenv_path = resolve_from_project(path)
    if not os.path.exists(dotenv_path):
        return
    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


def normalize_provider(name):
    v = (name or "").strip().lower()
    if v in {"google", "gemini"}:
        return "google"
    if v in {"openai", "oai"}:
        return "openai"
    if v in {"siliconflow", "sf", "硅基流动"}:
        return "siliconflow"
    if v in {"openrouter", "or"}:
        return "openrouter"
    return "google"


def request_proxies():
    http_proxy = (os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or "").strip()
    https_proxy = (os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or "").strip()
    if not http_proxy and https_proxy:
        http_proxy = https_proxy
    if not https_proxy and http_proxy:
        https_proxy = http_proxy
    if not http_proxy and not https_proxy:
        return None
    return {"http": http_proxy, "https": https_proxy}


def mask_key(key):
    if not key:
        return "(empty)"
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "..." + key[-4:]


def google_url(model, api_key):
    model_q = quote(model, safe="")
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model_q}:generateContent?key={api_key}"


def probe_google(api_key, model, timeout_s, proxies):
    url = google_url(model, api_key)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with exactly: pong"}]}],
        "generationConfig": {"temperature": 0},
    }
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout_s, proxies=proxies)
    data = resp.json() if resp.text else {}
    if not resp.ok:
        return False, {"http_status": resp.status_code, "provider_response": data}
    text = ""
    cands = data.get("candidates", [])
    if cands and isinstance(cands[0], dict):
        parts = ((cands[0].get("content") or {}).get("parts") or [])
        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)).strip()
    return True, {"http_status": resp.status_code, "response_text": text}


def probe_siliconflow(api_key, model, timeout_s, proxies):
    return probe_openai_compatible(
        SILICONFLOW_API_URL,
        api_key,
        model,
        timeout_s,
        proxies,
    )


def probe_openai(api_key, model, timeout_s, proxies):
    return probe_openai_compatible(
        f"{openai_base_url()}/chat/completions",
        api_key,
        model,
        timeout_s,
        proxies,
        reasoning_effort=(os.getenv("MODEL_REASONING_EFFORT") or "").strip(),
    )


def probe_openrouter(api_key, model, timeout_s, proxies):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    referer = (os.getenv("OPENROUTER_HTTP_REFERER") or "").strip()
    title = (os.getenv("OPENROUTER_TITLE") or "proof_agent").strip()
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-OpenRouter-Title"] = title
    return probe_openai_compatible(
        OPENROUTER_API_URL,
        api_key,
        model,
        timeout_s,
        proxies,
        headers=headers,
    )


def probe_openai_compatible(api_url, api_key, model, timeout_s, proxies, headers=None, reasoning_effort=""):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        "temperature": 0,
        "max_tokens": 16,
    }
    if str(reasoning_effort or "").strip():
        payload["reasoning_effort"] = str(reasoning_effort).strip()
    merged_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if isinstance(headers, dict):
        merged_headers.update(headers)
    resp = requests.post(
        api_url,
        headers=merged_headers,
        json=payload,
        timeout=timeout_s,
        proxies=proxies,
    )
    data = resp.json() if resp.text else {}
    if not resp.ok:
        return False, {"http_status": resp.status_code, "provider_response": data}
    text = ""
    choices = data.get("choices", [])
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        text = str(msg.get("content", "")).strip()
    return True, {"http_status": resp.status_code, "response_text": text}


def main():
    parser = argparse.ArgumentParser(description="Minimal LLM API connectivity test.")
    parser.add_argument("--provider", default="google", help="google, openai, siliconflow, or openrouter; default from env LLM_PROVIDER")
    parser.add_argument("--model", default="", help="model name; default from env MODEL_NAME")
    parser.add_argument("--timeout", type=int, default=30, help="request timeout seconds")
    parser.add_argument("--json", action="store_true", help="print full JSON result")
    args = parser.parse_args()

    load_dotenv_file(ENV_FILE)
    provider = normalize_provider(args.provider or os.getenv("LLM_PROVIDER", "google"))
    model = (args.model or os.getenv("MODEL_NAME") or "").strip()
    proxies = request_proxies()

    if not model:
        if provider == "google":
            model = "gemini-3.1-pro-preview"
        elif provider == "openai":
            model = "gpt-5.4"
        elif provider == "openrouter":
            model = "anthropic/claude-opus-4.6"
        else:
            model = "Pro/deepseek-ai/DeepSeek-V3.2"

    if provider == "google":
        api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    elif provider == "openai":
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    elif provider == "openrouter":
        api_key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    else:
        api_key = (os.getenv("SILICONFLOW_API_KEY") or "").strip()
    if not api_key:
        print(json.dumps({"ok": False, "reason": f"missing api key for provider={provider}"}, ensure_ascii=False, indent=2))
        return 2

    result = {
        "provider": provider,
        "model": model,
        "timeout_s": args.timeout,
        "api_key": mask_key(api_key),
        "proxies": proxies or {},
    }

    try:
        if provider == "google":
            ok, detail = probe_google(api_key, model, args.timeout, proxies)
        elif provider == "openai":
            ok, detail = probe_openai(api_key, model, args.timeout, proxies)
        elif provider == "openrouter":
            ok, detail = probe_openrouter(api_key, model, args.timeout, proxies)
        else:
            ok, detail = probe_siliconflow(api_key, model, args.timeout, proxies)
        result["ok"] = ok
        result["detail"] = detail
    except requests.RequestException as e:
        result["ok"] = False
        result["detail"] = {"error_type": type(e).__name__, "error": str(e)}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"ok={result['ok']} provider={provider} model={model}")
        print(f"proxy={result['proxies']}")
        print(json.dumps(result["detail"], ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
