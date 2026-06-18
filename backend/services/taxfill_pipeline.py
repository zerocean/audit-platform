"""TaxFill 流水线 — 异步封装"""
import os, json, subprocess, asyncio
from config import TAXFILL_HK_DIR


def _find_python() -> str:
    for cmd in ["python3", "python", r"D:\Python\python.exe"]:
        try:
            subprocess.run([cmd, "--version"], capture_output=True, timeout=5, check=True)
            return cmd
        except Exception:
            continue
    return "python"


async def run_taxfill_pipeline(fs_pdf_path: str, taxcomp_pdf_path: str) -> dict:
    """异步运行 TaxFill 全流水线"""
    python_cmd = _find_python()
    script = os.path.join(TAXFILL_HK_DIR, "pipeline.py")
    if not os.path.exists(script):
        raise FileNotFoundError(f"Pipeline 脚本不存在: {script}")

    env = os.environ.copy()
    if "DASHSCOPE_API_KEY" not in env or not env["DASHSCOPE_API_KEY"]:
        env["DASHSCOPE_API_KEY"] = os.environ.get("DASHSCOPE_API_KEY", "")

    loop = asyncio.get_event_loop()
    proc = await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            [python_cmd, script, fs_pdf_path, taxcomp_pdf_path],
            capture_output=True, text=True, timeout=3600, env=env, cwd=TAXFILL_HK_DIR,
        )
    )
    if proc.returncode != 0:
        raise RuntimeError(f"TaxFill Pipeline 失败: {proc.stderr[:3000]}")

    # Parse token usage from stdout
    token_usage = 0
    vision_tokens = 0
    filling_tokens = 0
    import re as _re
    m = _re.search(r'\[TOKEN_USAGE\] ({.*?})', proc.stdout)
    if m:
        try:
            data = json.loads(m.group(1))
            token_usage = data.get('total_tokens', 0)
            vision_tokens = data.get('vision_tokens', 0)
            filling_tokens = data.get('filling_tokens', 0)
        except Exception: pass

    # 查找最新的日志目录
    logs_dir = os.path.join(TAXFILL_HK_DIR, "logs")
    run_id = None
    if os.path.exists(logs_dir):
        subdirs = sorted([d for d in os.listdir(logs_dir) if os.path.isdir(os.path.join(logs_dir, d))], reverse=True)
        for d in subdirs:
            if os.path.exists(os.path.join(logs_dir, d, "filling")):
                run_id = d; break
    if not run_id:
        raise RuntimeError(f"Pipeline 未找到输出目录")

    log_dir = os.path.join(logs_dir, run_id)
    filling_dir = os.path.join(log_dir, "filling")

    result = {"run_id": run_id, "token_usage": token_usage,
              "vision_tokens": vision_tokens, "filling_tokens": filling_tokens}
    for name in ["fs_parsed.ton", "taxcomp_parsed.ton"]:
        p = os.path.join(log_dir, "fs" if name.startswith("fs") else "taxcomp", name)
        if os.path.exists(p): result["fs_ton_path" if name.startswith("fs") else "taxcomp_ton_path"] = p

    for name in ["filling_reference.json", "filling_reference.xlsx", "filling_reference.ton"]:
        p = os.path.join(filling_dir, name)
        if os.path.exists(p):
            key = {"filling_reference.json": "json_path", "filling_reference.xlsx": "excel_path", "filling_reference.ton": "ton_path"}[name]
            result[key] = p
            if name.endswith(".json"):
                with open(p, "r", encoding="utf-8") as f:
                    result["filling_json"] = json.load(f)
    return result
