"""
审计 Parser — 异步封装
"""
import os, json, subprocess, tempfile, asyncio
from config import AUDIT_REVIEW_DIR, OUTPUTS_DIR, INSPECTOR_MODEL


async def run_vision_parser(pdf_path: str, output_path: str = None) -> dict:
    """异步调用 Vision Parser"""
    if output_path is None:
        output_path = os.path.join(OUTPUTS_DIR, f"parsed_{os.path.basename(pdf_path)}.json")
    script = os.path.join(AUDIT_REVIEW_DIR, "parser_vision_json_old.py")
    if not os.path.exists(script):
        raise FileNotFoundError(f"Parser 脚本不存在: {script}")
    python_cmd = _find_python()
    env = os.environ.copy()
    if "DASHSCOPE_API_KEY" not in env or not env["DASHSCOPE_API_KEY"]:
        env["DASHSCOPE_API_KEY"] = os.environ.get("DASHSCOPE_API_KEY", "")

    loop = asyncio.get_event_loop()
    proc = await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            [python_cmd, script, pdf_path, output_path],
            capture_output=True, text=True, timeout=1800, env=env, cwd=AUDIT_REVIEW_DIR,
        )
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Vision Parser 失败: {proc.stderr[:2000]}")
    if not os.path.exists(output_path):
        raise RuntimeError(f"Parser 未生成输出文件: {output_path}")
    with open(output_path, "r", encoding="utf-8") as f:
        raw = f.read()
    data = json.loads(raw)

    # Extract token usage from output JSON
    token_usage = data.pop("_token_usage", 0)
    actual_model = data.pop("_model", None)

    return {"data": data, "raw": raw, "output_path": output_path,
            "token_usage": token_usage, "model": actual_model}


async def run_inspector(pdf_path: str, output_path: str = None) -> dict:
    """异步调用 Inspector"""
    if output_path is None:
        output_path = os.path.join(tempfile.gettempdir(), f"inspect_output_{os.getpid()}.json")
    script = os.path.join(AUDIT_REVIEW_DIR, "inspector.py")
    if not os.path.exists(script):
        raise FileNotFoundError(f"Inspector 脚本不存在: {script}")
    python_cmd = _find_python()
    env = os.environ.copy()
    if "DASHSCOPE_API_KEY" not in env or not env["DASHSCOPE_API_KEY"]:
        env["DASHSCOPE_API_KEY"] = os.environ.get("DASHSCOPE_API_KEY", "")

    loop = asyncio.get_event_loop()
    proc = await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            [python_cmd, script, pdf_path, output_path],
            capture_output=True, text=True, timeout=1800, env=env, cwd=AUDIT_REVIEW_DIR,
        )
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Inspector 失败: {proc.stderr[:2000]}")
    if not os.path.exists(output_path):
        raise RuntimeError(f"Inspector 未生成输出: {output_path}")
    with open(output_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    data["_token_usage"] = data.get("_token_usage", 0)
    token_usage = data.pop("_token_usage", 0)
    data["token_usage"] = token_usage
    data["_model"] = data.get("model", INSPECTOR_MODEL)

    try: os.unlink(output_path)
    except Exception: pass
    return data


def _find_python() -> str:
    for cmd in ["python3", "python", r"D:\Python\python.exe"]:
        try:
            subprocess.run([cmd, "--version"], capture_output=True, timeout=5, check=True)
            return cmd
        except Exception:
            continue
    return "python"
