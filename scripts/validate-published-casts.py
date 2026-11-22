#!/usr/bin/env python3
"""Validate asciicast structure before publishing external recordings."""
import json
from pathlib import Path
import sys

if len(sys.argv)!=3:
    raise SystemExit("usage: validate-published-casts.py NAME CAST")
name,path=sys.argv[1:]
if name not in ("vcli-playground.cast","vcli-playground-saas.cast"):
    raise SystemExit("Unexpected recording name")
p=Path(path)
if not p.is_file() or not 256<p.stat().st_size<25_000_000:
    raise SystemExit("Missing or oversized recording")
with p.open(encoding="utf-8") as f:
    head=json.loads(f.readline())
    if head.get("version") not in (2,3):
        raise SystemExit("Not asciicast v2/v3")
    found=set()
    count=0
    previous=-1.0
    required=("scripts/playground.sh shell", "Vault backup restored:") if name=="vcli-playground.cast" else ("tenant-032", "REST acceptance PASS: 8640 restored values")
    tail=""
    for raw in f:
        event=json.loads(raw)
        if len(event)!=3 or event[0]<previous:
            raise SystemExit("Invalid event")
        previous=event[0]
        count+=1
        if event[1]=="o":
            tail=(tail+event[2])[-5000:]
            for marker in required:
                if marker in tail:found.add(marker)
    if count<100 or len(found)!=len(required):
        raise SystemExit("Recording incomplete or missing acceptance markers")
print("Verified",name,count,"terminal events")
