#!/usr/bin/env python3
"""
ProfitsPilot - Pipeline
Stage 1 (vision_parser) → Stage 2 (filling_engine)

Input:  FS PDF + Tax Computation PDF
Output: Filling reference (TON / JSON / Excel)
"""

import subprocess
import sys
import os
from datetime import datetime


def main():
    if len(sys.argv) < 3:
        print("Usage: python pipeline.py <fs_pdf> <taxcomp_pdf>")
        print("  fs_pdf:     Director's Report & Financial Statements")
        print("  taxcomp_pdf: Tax Computation")
        sys.exit(1)

    fs_pdf = sys.argv[1]
    taxcomp_pdf = sys.argv[2]

    base_dir = os.path.dirname(os.path.abspath(__file__))
    schema = os.path.join(base_dir, "tax_return_schema.ton")
    python_exe = sys.executable

    for path in [fs_pdf, taxcomp_pdf, schema]:
        if not os.path.exists(path):
            print(f"[ERROR] File not found: {path}")
            sys.exit(1)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(base_dir, "logs", run_id)

    # ========== Stage 1: Vision Parser ==========
    print("=" * 60)
    print("STAGE 1: Vision Parser (PDF → TON)")
    print("=" * 60)

    result = subprocess.run(
        [python_exe, "vision_parser.py", fs_pdf, taxcomp_pdf, log_dir],
        cwd=base_dir, capture_output=True, text=True,
    )
    # Relay subprocess output
    if result.stdout: print(result.stdout)
    if result.stderr: print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        print(f"[ERROR] Vision Parser failed with code {result.returncode}")
        sys.exit(1)

    # Parse vision parser token usage from its stdout
    import re as _re, json as _json
    vision_tokens = 0
    vision_input = 0
    vision_output = 0
    vm = _re.search(r'\[TOKEN_USAGE\] ({.*?})', result.stdout)
    if vm:
        try:
            data = _json.loads(vm.group(1))
            vision_tokens = data.get('total_tokens', 0)
            vision_input = data.get('input_tokens', 0)
            vision_output = data.get('output_tokens', 0)
        except Exception: pass

    # ========== Stage 2: Filling Engine ==========
    fs_ton = os.path.join(log_dir, "fs", "fs_parsed.ton")
    taxcomp_ton = os.path.join(log_dir, "taxcomp", "taxcomp_parsed.ton")
    filling_dir = os.path.join(log_dir, "filling")

    print()
    print("=" * 60)
    print("STAGE 2: Filling Engine (TON → Filling Reference)")
    print("=" * 60)

    result = subprocess.run(
        [
            python_exe, "filling_engine.py",
            "--fs-ton", fs_ton,
            "--taxcomp-ton", taxcomp_ton,
            "--schema", schema,
            "--output-dir", filling_dir,
        ],
        cwd=base_dir, capture_output=True, text=True,
    )
    if result.stdout: print(result.stdout)
    if result.stderr: print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        print(f"[ERROR] Filling Engine failed with code {result.returncode}")
        sys.exit(1)

    # Parse filling engine token usage
    filling_tokens = 0
    filling_input = 0
    filling_output = 0
    fm = _re.search(r'\[TOKEN_USAGE\] ({.*?})', result.stdout)
    if fm:
        try:
            data = _json.loads(fm.group(1))
            filling_tokens = data.get('total_tokens', 0)
            filling_input = data.get('input_tokens', 0)
            filling_output = data.get('output_tokens', 0)
        except Exception: pass

    # ========== Done ==========
    print()
    print("=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"Run directory: {log_dir}")
    print(f"  FS TON:        {fs_ton}")
    print(f"  TaxComp TON:   {taxcomp_ton}")
    print(f"  Filling ref:   {os.path.join(filling_dir, 'filling_reference.ton')}")
    print(f"  Excel:         {os.path.join(filling_dir, 'filling_reference.xlsx')}")
    print(f"  JSON:          {os.path.join(filling_dir, 'filling_reference.json')}")

    # Aggregate token usage
    import json as _json2
    total = vision_tokens + filling_tokens
    print(f"\n[TOKEN_USAGE] {_json2.dumps({'vision_tokens': vision_tokens, 'vision_input': vision_input, 'vision_output': vision_output, 'filling_tokens': filling_tokens, 'filling_input': filling_input, 'filling_output': filling_output, 'total_tokens': total})}")


if __name__ == "__main__":
    main()
