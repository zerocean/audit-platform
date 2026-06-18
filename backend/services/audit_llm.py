"""
LLM 审计服务 — 直接调 DashScope API (SSE 流)
从 server.js streamChat + fetchAuditReportSync 迁移
"""
import json
import asyncio
from typing import AsyncGenerator
from openai import OpenAI
from config import (
    DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL,
    AUDIT_MODEL, AUDIT_MAX_TOKENS,
)
import os

# 初始化 OpenAI 客户端 (DashScope 兼容接口)
client = OpenAI(
    base_url=DASHSCOPE_BASE_URL,
    api_key=DASHSCOPE_API_KEY,
)

# 加载系统提示词
def _load_system_prompt() -> str:
    """加载审计 LLM 系统提示词"""
    prompt_paths = [
        os.path.join(os.path.dirname(__file__), "../prompts/prompt_json_concise.md"),
    ]
    for p in prompt_paths:
        real = os.path.abspath(p)
        if os.path.exists(real):
            with open(real, "r", encoding="utf-8") as f:
                raw = f.read()
            # 清理特殊字符 (同 server.js)
            return (
                raw.replace("&#x20;", " ")
                   .replace("&amp;", "&")
                   .replace("\n{3,}", "\n\n")
                   .strip()
            )
    return "You are a financial audit expert. Review the structured data and identify errors."


SYSTEM_PROMPT = None  # 延迟加载


def _get_system_prompt() -> str:
    global SYSTEM_PROMPT
    if SYSTEM_PROMPT is None:
        SYSTEM_PROMPT = _load_system_prompt()
    return SYSTEM_PROMPT


def _build_audit_message(json_data: dict, filename: str) -> str:
    """构造审计消息 (复用 server.js 的消息模板)"""
    json_text = json.dumps(json_data, ensure_ascii=False, indent=2)
    MAX_CHARS = 120000
    trimmed = json_text[:MAX_CHARS] + ("\n... [truncated]" if len(json_text) > MAX_CHARS else "")

    return f"""请对以下结构化财务数据执行审计复核任务。数据来源: {filename}。

数据格式说明（与 vision-parser 输出严格对齐）:
- 顶层为 pages 数组，每个 page 包含 page_number（页码）和 tables 数组
- 每个 table 包含:
  - table_name: 表格标题原文
  - is_note: true 表示属于附注（NOTES），false 表示属于主表（资产负债表、利润表等）
  - note_number: 附注编号（如 "6"、"9"），仅当 is_note=true 时有值，否则为 null
  - columns: 列头列表，可能是年份（如 "2024"、"2023"）或描述性标签（如 "Cost"、"Total"）
  - is_continuation: true 表示该表是跨页续表（标题含 Continued / 续）
  - continued_from: 续表指向的原表名，is_continuation=false 时为 null
  - rows 数组，每行包含:
    - row_id: 表内唯一标识，格式为 <table_name>_<index>
    - label: 科目/行项目名称原文
    - role: 行角色 — header(标题无数字)、detail(明细有数字)、subtotal(分组小计)、grand_total(总计)、calculated(跨组计算结果如 gross profit)、text(非表格文本)
    - level: 缩进层级（0=顶层，1=缩进，2=进一步缩进），用于推断层级关系
    - section: 功能分组（如 "revenue"、"cost_of_sales"）
    - group_path: 嵌套分组路径（如 ["Administrative expenses", "Staff costs"]），用于匹配明细与对应小计
    - note_ref: 行内注释引用编号（如 "6"、"12"），用于交叉引用检查；无则为 null
    - values: {{列名: 原文字符串值}} 字典。所有数字均为原文字符串，未做数值修正；缺失值为 null

请基于以下 JSON 数据执行五阶段审计分析（Document Mapping → Within-Table Arithmetic → Data Tie-Out → Cross-Reference → Transcription Check）。
- 处理跨页续表（is_continuation=true）时不要重复计算行
- 利用 group_path 和 level 重建层级，不要仅依赖 label
- 所有数字按原文字符串处理，对明显录入错误（如 "10,0000"）在 Phase 5 中标记

```json
{trimmed}
```"""


async def run_audit_stream(json_data: dict, filename: str = "data.json") -> AsyncGenerator[str, None]:
    """
    SSE 流式审计
    yield: "data: content" / "data: [THINK]reasoning[/THINK]" / "data: [DONE]" / "data: [ERROR] msg"
    """
    message = _build_audit_message(json_data, filename)

    try:
        stream = client.chat.completions.create(
            model=AUDIT_MODEL,
            messages=[
                {"role": "system", "content": _get_system_prompt()},
                {"role": "user", "content": message},
            ],
            stream=True,
            max_tokens=AUDIT_MAX_TOKENS,
            temperature=0.3,
            stream_options={"include_usage": True},
        )

        reasoning_buf = ""
        reasoning_sent = False
        total_usage = None

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            
            # 捕获 token usage (最后一个chunk)
            if hasattr(chunk, 'usage') and chunk.usage:
                total_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }

            if delta is None:
                continue

            # 处理思考内容
            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                reasoning_buf += delta.reasoning_content

            content = getattr(delta, 'content', None) or ""

            # 首次有 content 时先发送思考内容
            if content and reasoning_buf and not reasoning_sent:
                escaped = reasoning_buf.replace("\n", "⏎")
                yield f"data: [THINK]{escaped}[/THINK]\n\n"
                reasoning_sent = True

            if content:
                escaped = content.replace("\n", "⏎")
                yield f"data: {escaped}\n\n"

        # 兜底: 只有思考没有输出时发送
        if reasoning_buf and not reasoning_sent:
            escaped = reasoning_buf.replace("\n", "⏎")
            yield f"data: [THINK]{escaped}[/THINK]\n\n"

        if total_usage:
            yield f"data: [USAGE] {json.dumps(total_usage)}\n\n"

        yield "data: [DONE]\n\n"

    except Exception as e:
        yield f"data: [ERROR] {str(e)}\n\n"


async def run_audit_sync(json_data: dict, filename: str = "data.json"):
    """
    同步审计 — 返回 (text, usage_dict)
    """
    message = _build_audit_message(json_data, filename)

    response = client.chat.completions.create(
        model=AUDIT_MODEL,
        messages=[
            {"role": "system", "content": _get_system_prompt()},
            {"role": "user", "content": message},
        ],
        max_tokens=AUDIT_MAX_TOKENS,
        temperature=0.3,
    )

    usage = None
    if hasattr(response, 'usage') and response.usage:
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }

    return response.choices[0].message.content or "", usage
