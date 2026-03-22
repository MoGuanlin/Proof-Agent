#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from app_config import (
        MARKER_DISABLE_MULTIPROCESSING,
        MARKER_EXTRA_ARGS,
        MARKER_FORCE_CPU,
        MARKER_FORCE_OCR,
        MARKER_TIMEOUT_SECONDS,
    )
except Exception:
    MARKER_FORCE_OCR = False
    MARKER_FORCE_CPU = False
    MARKER_DISABLE_MULTIPROCESSING = True
    MARKER_EXTRA_ARGS = []
    MARKER_TIMEOUT_SECONDS = 1800


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用 marker 将 PDF 转成 Markdown，支持单文件、目录和 glob 批量转换。",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="输入的 PDF 文件、目录，或 glob 模式，如 cite_file/*.pdf",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=".cache/markitdown",
        help="Markdown 输出目录，默认是 .cache/markitdown",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="当输入是目录时，递归查找其中的 PDF 文件。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使输出文件已存在，也强制重新转换。",
    )
    parser.add_argument(
        "--filename-mode",
        choices=("cache", "stem"),
        default="cache",
        help="输出文件命名方式。cache 与主系统缓存命名兼容；stem 生成更直观的文件名。",
    )
    parser.add_argument(
        "--page-range",
        default="",
        help="只转换指定页码，格式与 marker 一致，例如 0,5-10,20",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=MARKER_TIMEOUT_SECONDS,
        help=f"单个 PDF 转换超时时间（秒），默认 {MARKER_TIMEOUT_SECONDS}",
    )
    parser.add_argument(
        "--marker-extra-arg",
        action="append",
        default=[],
        help="额外传给 marker 的参数，可重复使用多次。",
    )

    force_ocr_group = parser.add_mutually_exclusive_group()
    force_ocr_group.add_argument(
        "--force-ocr",
        dest="force_ocr",
        action="store_true",
        help="强制使用 OCR。",
    )
    force_ocr_group.add_argument(
        "--no-force-ocr",
        dest="force_ocr",
        action="store_false",
        help="显式关闭 OCR 强制模式。",
    )
    parser.set_defaults(force_ocr=MARKER_FORCE_OCR)

    force_cpu_group = parser.add_mutually_exclusive_group()
    force_cpu_group.add_argument(
        "--force-cpu",
        dest="force_cpu",
        action="store_true",
        help="强制 marker 走 CPU。",
    )
    force_cpu_group.add_argument(
        "--no-force-cpu",
        dest="force_cpu",
        action="store_false",
        help="显式关闭 CPU 强制模式。",
    )
    parser.set_defaults(force_cpu=MARKER_FORCE_CPU)

    multiprocessing_group = parser.add_mutually_exclusive_group()
    multiprocessing_group.add_argument(
        "--disable-multiprocessing",
        dest="disable_multiprocessing",
        action="store_true",
        help="禁用 marker 多进程。",
    )
    multiprocessing_group.add_argument(
        "--enable-multiprocessing",
        dest="disable_multiprocessing",
        action="store_false",
        help="启用 marker 多进程。",
    )
    parser.set_defaults(disable_multiprocessing=MARKER_DISABLE_MULTIPROCESSING)
    return parser


def _marker_env(force_cpu: bool) -> dict[str, str]:
    env = os.environ.copy()
    if force_cpu:
        env["TORCH_DEVICE"] = "cpu"
        env["CUDA_VISIBLE_DEVICES"] = ""
    return env


def _marker_tag(force_ocr: bool, force_cpu: bool, disable_multiprocessing: bool, extra_args: list[str]) -> str:
    marker_cfg = (
        f"fo{1 if force_ocr else 0}_"
        f"fc{1 if force_cpu else 0}_"
        f"dmp{1 if disable_multiprocessing else 0}_"
        f"{' '.join(extra_args)}"
    )
    return hashlib.sha1(marker_cfg.encode("utf-8")).hexdigest()[:10]


def _cache_named_output_path(
    pdf_path: Path,
    output_dir: Path,
    force_ocr: bool,
    force_cpu: bool,
    disable_multiprocessing: bool,
    extra_args: list[str],
) -> Path:
    abs_pdf_path = str(pdf_path.resolve())
    path_hash = hashlib.sha1(abs_pdf_path.encode("utf-8")).hexdigest()[:12]
    marker_tag = _marker_tag(force_ocr, force_cpu, disable_multiprocessing, extra_args)
    return output_dir / f"{pdf_path.stem}.{path_hash}.marker.{marker_tag}.md"


def _stem_named_output_path(pdf_path: Path, output_dir: Path) -> Path:
    candidate = output_dir / f"{pdf_path.stem}.md"
    if not candidate.exists():
        return candidate
    path_hash = hashlib.sha1(str(pdf_path.resolve()).encode("utf-8")).hexdigest()[:8]
    return output_dir / f"{pdf_path.stem}.{path_hash}.md"


def _resolve_output_path(
    pdf_path: Path,
    output_dir: Path,
    filename_mode: str,
    force_ocr: bool,
    force_cpu: bool,
    disable_multiprocessing: bool,
    extra_args: list[str],
) -> Path:
    if filename_mode == "stem":
        return _stem_named_output_path(pdf_path, output_dir)
    return _cache_named_output_path(
        pdf_path,
        output_dir,
        force_ocr=force_ocr,
        force_cpu=force_cpu,
        disable_multiprocessing=disable_multiprocessing,
        extra_args=extra_args,
    )


def _discover_pdf_paths(inputs: list[str], recursive: bool) -> list[Path]:
    discovered: list[Path] = []
    seen: set[Path] = set()
    for raw_input in inputs:
        matches = glob.glob(os.path.expanduser(raw_input))
        candidates = [Path(p) for p in matches] if matches else [Path(os.path.expanduser(raw_input))]
        for candidate in candidates:
            if candidate.is_dir():
                iterator = candidate.rglob("*.pdf") if recursive else candidate.glob("*.pdf")
                iterator_upper = candidate.rglob("*.PDF") if recursive else candidate.glob("*.PDF")
                for pdf_path in list(iterator) + list(iterator_upper):
                    resolved = pdf_path.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        discovered.append(pdf_path)
                continue
            if candidate.is_file() and candidate.suffix.lower() == ".pdf":
                resolved = candidate.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    discovered.append(candidate)
    discovered.sort(key=lambda path: str(path))
    return discovered


def _collect_marker_output(out_dir: str) -> str:
    md_candidates = sorted(Path(out_dir).rglob("*.md"))
    if not md_candidates:
        raise RuntimeError("marker produced no markdown")
    return md_candidates[0].read_text(encoding="utf-8")


def _run_marker(
    pdf_path: Path,
    *,
    force_ocr: bool,
    force_cpu: bool,
    disable_multiprocessing: bool,
    page_range: str,
    extra_args: list[str],
    timeout: int,
) -> str:
    marker_single = shutil.which("marker_single")
    marker_cli = shutil.which("marker")
    if not marker_single and not marker_cli:
        raise RuntimeError("marker CLI not found; install marker-pdf first")

    with tempfile.TemporaryDirectory(prefix="marker_out_") as out_dir, tempfile.TemporaryDirectory(
        prefix="marker_in_"
    ) as in_dir:
        if marker_single:
            cmd = [marker_single, str(pdf_path), "--output_dir", out_dir, "--output_format", "markdown"]
        else:
            staged_pdf = Path(in_dir) / pdf_path.name
            shutil.copy2(pdf_path, staged_pdf)
            cmd = [marker_cli, in_dir, "--output_dir", out_dir, "--output_format", "markdown"]

        if page_range:
            cmd.extend(["--page_range", page_range])
        if force_ocr:
            cmd.append("--PdfProvider_force_ocr")
        if disable_multiprocessing:
            cmd.append("--disable_multiprocessing")
        cmd.extend(extra_args)

        print(f"⏳ converting: {pdf_path}")
        print(f"   command: {' '.join(cmd)}")
        subprocess.run(
            cmd,
            check=True,
            env=_marker_env(force_cpu),
            timeout=timeout,
        )
        return _collect_marker_output(out_dir)


def _convert_one(
    pdf_path: Path,
    *,
    output_dir: Path,
    filename_mode: str,
    force: bool,
    force_ocr: bool,
    force_cpu: bool,
    disable_multiprocessing: bool,
    page_range: str,
    extra_args: list[str],
    timeout: int,
) -> tuple[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = _resolve_output_path(
        pdf_path,
        output_dir,
        filename_mode=filename_mode,
        force_ocr=force_ocr,
        force_cpu=force_cpu,
        disable_multiprocessing=disable_multiprocessing,
        extra_args=extra_args,
    )

    if output_path.exists() and not force and output_path.stat().st_mtime >= pdf_path.stat().st_mtime:
        print(f"📦 cache hit: {output_path}")
        return "cached", output_path

    markdown = _run_marker(
        pdf_path,
        force_ocr=force_ocr,
        force_cpu=force_cpu,
        disable_multiprocessing=disable_multiprocessing,
        page_range=page_range,
        extra_args=extra_args,
        timeout=timeout,
    )
    output_path.write_text(markdown, encoding="utf-8")
    print(f"💾 saved: {output_path}")
    return "converted", output_path


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    pdf_paths = _discover_pdf_paths(args.inputs, recursive=args.recursive)
    if not pdf_paths:
        parser.error("没有找到任何 PDF 文件。")

    output_dir = Path(args.output_dir).expanduser()
    extra_args = [*MARKER_EXTRA_ARGS, *args.marker_extra_arg]

    converted = 0
    cached = 0
    failed = 0

    for pdf_path in pdf_paths:
        try:
            status, _ = _convert_one(
                pdf_path,
                output_dir=output_dir,
                filename_mode=args.filename_mode,
                force=args.force,
                force_ocr=args.force_ocr,
                force_cpu=args.force_cpu,
                disable_multiprocessing=args.disable_multiprocessing,
                page_range=args.page_range,
                extra_args=extra_args,
                timeout=args.timeout,
            )
            if status == "cached":
                cached += 1
            else:
                converted += 1
        except Exception as exc:
            failed += 1
            print(f"❌ failed: {pdf_path} -> {exc}", file=sys.stderr)

    print(
        "✅ done: "
        f"total={len(pdf_paths)} converted={converted} cached={cached} failed={failed} output_dir={output_dir}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
