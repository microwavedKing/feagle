"""``feagle doctor``: report environment problems and how to fix them."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

from .. import doctor

if TYPE_CHECKING:
    import argparse

_SYMBOL = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL", "info": "--  "}


def run(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    checks = doctor.run_checks(cfg, verify_model_hash=not args.fast)

    if args.json:
        payload = [
            {"name": c.name, "status": c.status, "message": c.message, "hint": c.hint}
            for c in checks
        ]
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for check in checks:
            print(f"[{_SYMBOL.get(check.status, '?   ')}] {check.name}: {check.message}")
            if check.hint and check.status in ("warn", "fail"):
                print(f"         -> {check.hint}")

    failures = [c for c in checks if c.status == "fail"]
    if failures and not args.json:
        print(f"\n{len(failures)} problem(s) to fix.", file=sys.stderr)
    return 1 if failures else 0
