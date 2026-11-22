#!/usr/bin/env python3
"""Render measured GitHub Actions/SaaS durations. Never synthesize missing data."""
from __future__ import annotations

from datetime import datetime
from html import escape
import json
from pathlib import Path
import sys


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def draw(title, subtitle, rows, target, note):
    if not rows:
        raise ValueError("No actual timing observations")
    rows = [(str(label), float(seconds)) for label, seconds in rows]
    if any(not 0 <= seconds < 1e9 for _, seconds in rows):
        raise ValueError("Invalid duration")
    width, rowheight = 1000, 36
    height = 112 + rowheight * len(rows) + 32
    max_seconds = max([seconds for _, seconds in rows] + [0.001])
    content = [
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" viewBox="0 0 {width} {height}" aria-label="{escape(title, quote=True)}">',
        f'<rect width="{width}" height="{height}" rx="14" fill="#0a1410"/>',
        f'<text x="24" y="34" font-family="monospace" font-weight="bold" font-size="21" fill="#69f52b">{escape(title)}</text>',
        f'<text x="24" y="58" font-family="sans-serif" font-size="13" fill="#b1c6b7">{escape(subtitle)}</text>',
    ]
    for index, (label, seconds) in enumerate(rows):
        y = 80 + index * rowheight
        bar = max(2, round(seconds / max_seconds * 450))
        content.extend((
            f'<text x="24" y="{y+18}" font-family="monospace" font-size="12" fill="#e5f7e8">{escape(label[:52])}</text>',
            f'<rect x="468" y="{y}" width="450" height="24" rx="4" fill="#173122"/>',
            f'<rect x="468" y="{y}" width="{bar}" height="24" rx="4" fill="#69f52b"/>',
            f'<text x="990" y="{y+17}" text-anchor="end" font-family="monospace" font-size="13" fill="#c9f9b3">{seconds:.1f} s</text>',
        ))
    content.append(f'<text x="24" y="{height-16}" font-family="sans-serif" font-size="12" fill="#b1c6b7">{escape(note)}</text>')
    content.append("</svg>")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(content) + "\n", encoding="utf-8")


def render_jobs(source, output):
    data = json.loads(Path(source).read_text(encoding="utf-8"))
    rows = []
    for job in data.get("jobs", []):
        if job.get("conclusion") != "success":
            continue
        start, end = job.get("started_at"), job.get("completed_at")
        if not start or not end:
            continue
        rows.append((job["name"], (parse_time(end) - parse_time(start)).total_seconds()))
    rows.sort(key=lambda item: item[1], reverse=True)
    draw("vcli · CI jobs", "Successful jobs, measured wall time including preparation",
         rows, output, "Parallel jobs overlap; these times are not per-command latency.")


def render_saas(source, output):
    data = json.loads(Path(source).read_text(encoding="utf-8"))
    if data.get("status") != "passed" or data.get("profile") != "saas":
        raise ValueError("Only passed SaaS acceptance metrics can be published")
    operations = data.get("operations")
    if not isinstance(operations, list):
        raise ValueError("SaaS operations missing")
    rows = []
    for entry in operations:
        if not isinstance(entry, dict):
            raise ValueError("Invalid SaaS operation")
        seconds = entry.get("seconds")
        if not isinstance(seconds, (int, float)) or not 0 <= seconds < 1e9:
            raise ValueError("Invalid SaaS measurement")
        rows.append((entry.get("case") or entry.get("command") or "unknown", seconds))
    rows.sort(key=lambda item: item[1], reverse=True)
    draw("vcli · SaaS command durations",
         "12 longest commands of the verified 8640-secret acceptance (Enter to prompt)",
         rows[:12], output, "Measured in a real disposable Vault TLS; no synthetic time estimates.")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("jobs", "saas"):
        raise SystemExit("Usage: render-ci-reports.py jobs|saas INPUT.json OUTPUT.svg")
    {"jobs": render_jobs, "saas": render_saas}[sys.argv[1]](sys.argv[2], sys.argv[3])
