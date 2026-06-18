#!/usr/bin/env python3
"""
ProfitsPilot - Filling Engine
Stage 2: TON + Schema -> Filling Reference (TON/JSON/Excel)

Reads:
  - fs_parsed.ton (from vision_parser.py)
  - taxcomp_parsed.ton (from vision_parser.py)
  - tax_return_schema.ton (static schema definition)

Outputs:
  - filling_reference.ton (raw LLM output)
  - filling_reference.json (structured)
  - filling_reference.xlsx (human review, 3 sheets)
"""

import os
import sys
import json
import re
import argparse
import time
from datetime import datetime
from openai import OpenAI
import pandas as pd
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ========== 平台配置 ==========
PLATFORM = "dashscope"

CONFIG = {
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "text_model": "deepseek-v4-flash",
        # "text_model": "qwen3.6-flash",
        # "base_url": "https://api.deepseek.com",
        # "text_model": "deepseek-v4-flash",
    }
}

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
if not API_KEY:
    raise ValueError("DASHSCOPE_API_KEY not set")

TEXT_MODEL = CONFIG[PLATFORM]["text_model"]

client = OpenAI(
    base_url=CONFIG[PLATFORM]["base_url"],
    api_key=API_KEY,
)

# ========== Prompt C: 填表引擎 ==========
SYSTEM_PROMPT_FILLING = """
You are a Hong Kong Profits Tax Return (BIR51 2024/25) filling engine.

## INPUT TON SYNTAX (what you will receive)
- Objects start with: @type or @type key=value
- Properties: key: value (2-space indentation for children)
- Strings with spaces are in double quotes; strings with double quotes inside use single quotes
- null / true / false are unquoted literals
- Simple dict: {key1:val1, key2:val2}
- Lists: [item1, item2]

## FS TON FORMAT (Financial Statements)
- @text_block: narrative text (headers, footers, Director's Report, Auditor's Report)
- @table: tabular data with @column (column definitions) and @row (data rows)
- @row has: label, role (header|detail|subtotal|total|label|empty), level, is_bold, values dict, note_ref
- values dict maps column names to their values: {Note: 3, 2025 HK$: "2,133,015", 2024 HK$: "1,570,433"}

## TAX COMP TON FORMAT (Tax Computation)
- @text_block: headers, narrative, certification text
- @section: logical grouping with section_type (main_computation|schedule|working|narrative)
- @table: tabular data inside sections, using same @column + @row + values dict format as FS
- @row has: label, role, level, is_bold, values dict, schedule_ref, asset_type, allowance_type
- values dict maps column names to values: {Schedule: 1, HK$: 300, HK$ (subtotal): 12,100}
- To find amounts: look at @row.label for the line description, then read the matching column in values dict

## OUTPUT TON SYNTAX (what you must produce)
Use the exact same TON syntax. Each field is an @field object under its Part.

@filling_reference
  company_name: <extracted from @company_info>
  file_no: <extracted from @company_info>
  year_of_assessment: <extracted from @company_info>
  
  @validation_summary
    total_fields: <count>
    filled: <count>
    zero_filled: <count>
    manual_review: <count>
    errors: <count>
    warnings: <count>

  @part part_number=1
    part_name: "STATEMENT OF ASSESSABLE PROFITS OR ADJUSTED LOSS"
    @field box=1 field_id=1.1
      name_en: "Assessable Profits (before loss brought forward)"
      name_zh: "应评税利润（结转亏损前）"
      suggested_value: <amount or 0>
      value_type: amount
      fill_rule: "Exclude cents, enter 0 if NIL"
      source: tax_computation|financial_statements|derived|fixed|manual
      source_location: <exact location in source>
      source_evidence: <verbatim quote>
      mapping_rule: <rule name>
      confidence: high|medium|low
      reasoning: <natural language>
      validation_status: pass|fail|warning
      validation_notes: <notes>
      needs_manual_review: true|false
      review_reason: <explanation if true>

## CORE RULES

### RULE 0: HOW TO USE THE SCHEMA
The schema (tax_return_schema.ton) is the SINGLE SOURCE OF TRUTH. Every @field contains:
- `source_preference`: where to look (tax_computation / financial_statements / derived / fixed / manual)
- `source_keywords`: list of keywords to match in the source data
- `fill_rule`: how to format the value
- `decision_tree`: step-by-step logic (for derived fields)
- `validation_rules`: cross-checks to perform
- `mutually_exclusive_with`: fields that cannot both have values

**For EVERY field, read its schema definition first, then follow these rules:**

#### A) source_preference = tax_computation
Search the Tax Computation TON (`@section` → `@table` → `@row`):
1. Match `@row.label` against `source_keywords` (case-insensitive).
2. CRITICAL: Also apply SEMANTIC MATCHING (see RULE 1). Keywords are mere hints. You MUST use accounting judgment to find related items even when no keyword matches.
3. Use STRUCTURAL CONTEXT: items under "Less:" are deductions/exemptions; items under "Add:" are add-backs. This context tells you the NATURE of the item.
   - Example: "Bank interest income" under "Less:" → likely exempt interest → map to an exemption box.
   - Example: "Depreciation" under "Add:" → non-deductible depreciation added back → map to depreciation adjustment.
4. Extract the amount from `@row.values` dict (the HK$ column). Ignore parentheses — they indicate direction, not negative value.
5. If not found after semantic matching, try Financial Statements as fallback, then 0.

#### B) source_preference = financial_statements
Search the FS TON (`@table` → `@row`):
1. Match `@row.label` against `source_keywords`.
2. Apply SEMANTIC MATCHING.
3. Extract the amount from `@row.values` dict (usually "2025 HK$" column).
4. If not found, 0.

#### C) source_preference = derived
Follow the `decision_tree` in the schema:
1. Read each `step` in order.
2. The step references can use Box numbers (e.g., "IF Box 1.1 == 0 THEN No").
3. Execute the tree and output the result.
4. If uncertain, follow the `default_if_uncertain` instruction.

#### D) source_preference = fixed
Use the schema's `fill_rule` directly. No searching needed.
- If `value_type: label`, this is a display-only field. Set `suggested_value: ""` and `source: fixed`.

#### E) source_preference = manual
Set `suggested_value: ""`, `needs_manual_review: true`, `confidence: low`.

### RULE 1: INTELLIGENT MATCHING (ACCOUNTING EXPERTISE FIRST)
The `source_keywords` in the schema are HINTS, not rules. Your primary tool is accounting expertise.

**How to find values — in priority order:**

1. **ACCOUNTING KNOWLEDGE**: Use your expertise to identify which line items in the source data naturally correspond to the field. Ask yourself: "As an accountant, what item in this financial data would I map to this form field?"
   - For exemption/deduction fields: look for items being deducted or treated as non-taxable.
   - For income fields: look for revenue items.
   - For expense fields: look for cost/expense items.
   - For tax fields: look for tax-specific lines.

2. **STRUCTURAL CONTEXT**: Consider WHERE an item appears.
   - In Tax Computation: items under "Less:" are deductions; items under "Add:" are add-backs.
   - In Income Statement: items under "Revenue" are income; items under "Administrative expenses" are costs.
   - In Balance Sheet: items under "Current assets" vs "Non-current assets" tell you the nature.

3. **KEYWORD HINTS** (lowest priority): Only use `source_keywords` as a fallback confirmation. If your accounting judgment and the keywords disagree, trust your accounting judgment.

**Confidence levels:**
- Clear accounting mapping (e.g., "Revenue" → Box 106 Turnover) → confidence=high
- Reasonable inference from structure/context → confidence=medium
- Uncertain mapping → confidence=low + needs_manual_review=true

### RULE 2: PART 11 DEPRECIATION ALLOWANCES
Depreciation schedules use `@section(section_type=schedule)` → `@table` → `@row`.
Each `@row` carries `asset_type` and `allowance_type` fields.
Map to Part 11 boxes using the schema's `source_keywords` which encode the asset_type+allowance_type combinations.

IF Tax Computation provides ONLY aggregated depreciation without schedule breakdown:
- Set ALL Part 11 boxes to 0
- Add needs_manual_review: true
- review_reason: "Tax Computation shows aggregated depreciation of X. Schedule breakdown required."

### RULE 3: CROSS-VALIDATION
After generating all fields, validate using each field's `validation_rules`:
- `must_equal_taxcomp_X`: value must match the named Tax Computation line
- `mutually_exclusive_with`: two fields cannot both be > 0 (e.g., Box 110/111, Box 122/123)
- If validation fails → `validation_status: fail`, note the discrepancy
- If value missing or ambiguous → `confidence: low`, `needs_manual_review: true`

### RULE 4: MANUAL REVIEW HANDLING
When a field cannot be determined with certainty:
- `suggested_value`: "" (blank) for Yes/No; 0 for amounts UNLESS certain it should be something else
- `needs_manual_review`: true
- `review_reason`: Explain exactly what is missing and why human judgment is needed
- `confidence`: low

## OUTPUT REQUIREMENTS
- Output ONLY valid TON. No markdown code blocks, no explanations outside TON.
- Every Part from 1 to 13 must be present.
- Every box from 1 to 127 must have an @field entry.
- Box 42 (9.1) Form S1: No
- Box 43 (9.2) Form S2: No
- ... through Box 63 (9.22) Form S22: No
- reasoning: "Per company rule, supplementary forms are not applicable."
- LANGUAGE: review_reason and reasoning MUST be written in Chinese. Exceptions: professional terms (e.g., "Assessable profit", "Cost of sales"), proper nouns, and verbatim quotes from FS/TaxComp may remain in English.

### RULE 8: CROSS-VALIDATION
After generating all fields, validate:
1. Box 1.1 must equal Tax Computation "Assessable profit". If mismatch -> validation_status: fail.
2. Box 110 and 111 cannot both be > 0.
3. Box 122 and 123 cannot both be > 0.
4. Box 12.22 must equal FS "Issued share capital".
5. Box 12.23 must equal FS "Total assets".
6. All amount fields must have a value (0 if NIL). Never output null for amount fields.
7. If source evidence is missing or ambiguous, confidence=low and needs_manual_review=true.

### RULE 9: MANUAL REVIEW HANDLING
When a field cannot be determined with certainty:
- suggested_value: "" (blank string) for Yes/No; 0 for amounts UNLESS you are certain it should be 0.
- needs_manual_review: true
- review_reason: Explain exactly what is missing and why human judgment is needed.
- confidence: low

## OUTPUT REQUIREMENTS
- Output ONLY valid TON. No markdown code blocks, no explanations outside TON.
- Every Part from 1 to 13 must be present.
- Every box from 1 to 127 must have an @field entry.
- Part 9 boxes 42-63 must all be present with value "No".
- Include @validation_summary at the top.
- Include reasoning for every field.
"""


# ========== TON 解析器（轻量版）==========
class TONParser:
    """Lightweight TON parser for filling_engine internal use."""
    
    def __init__(self, ton_text: str):
        self.lines = ton_text.split('\n')
        self.pos = 0
        self.length = len(self.lines)
    
    def parse(self) -> dict:
        objects = []
        while self.pos < self.length:
            line = self.lines[self.pos]
            stripped = line.strip()
            if stripped.startswith('@'):
                obj = self._parse_object(indent=0)
                objects.append(obj)
            else:
                self.pos += 1
        return {"objects": objects}
    
    def _parse_object(self, indent: int) -> dict:
        line = self.lines[self.pos]
        match = re.match(r'^(\s*)@(\w+)(?:\s+(.+))?', line)
        if not match:
            raise ValueError(f"Invalid @ declaration at line {self.pos}: {line[:80]}")
        
        current_indent = len(match.group(1))
        obj_type = match.group(2)
        header_props_str = match.group(3) or ""
        
        self.pos += 1
        
        props = self._parse_inline_props(header_props_str)
        children = []
        
        while self.pos < self.length:
            next_line = self.lines[self.pos]
            if not next_line.strip():
                self.pos += 1
                continue
            
            next_indent = len(next_line) - len(next_line.lstrip())
            if next_indent <= current_indent:
                break
            
            if next_line.strip().startswith('@'):
                children.append(self._parse_object(indent=next_indent))
            else:
                prop = self._parse_property_line(next_line.strip())
                props.update(prop)
                self.pos += 1
        
        return {
            "type": obj_type,
            "properties": props,
            "children": children
        }
    
    def _parse_inline_props(self, s: str) -> dict:
        props = {}
        pattern = r'(\w+)=((?:\{[^}]*\}|"[^"]*"|\'[^\']*\'|\S+))'
        for m in re.finditer(pattern, s):
            k = m.group(1)
            v = m.group(2).strip()
            props[k] = self._convert_value(v, key=k)
        return props
    
    def _parse_property_line(self, line: str) -> dict:
        if ':' not in line:
            return {}
        
        colon_idx = -1
        brace_depth = 0
        for i, ch in enumerate(line):
            if ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
            elif ch == ':' and brace_depth == 0:
                colon_idx = i
                break
        
        if colon_idx == -1:
            return {}
        
        key = line[:colon_idx].strip()
        val = line[colon_idx+1:].strip()
        return {key: self._convert_value(val, key=key)}
    
    def _convert_value(self, v: str, key: str = None):
        v = v.strip()
        if v == 'null' or v == '':
            return None
        if v == 'true':
            return True
        if v == 'false':
            return False
        if v.startswith('"') and v.endswith('"'):
            return v[1:-1]
        if v.startswith("'") and v.endswith("'"):
            return v[1:-1]
        if v.startswith('{') and v.endswith('}'):
            inner = v[1:-1]
            result = {}
            if not inner.strip():
                return result
            items = self._split_dict_items(inner)
            for item in items:
                if ':' not in item:
                    continue
                k, val = item.split(':', 1)
                result[k.strip()] = self._convert_value(val.strip())
            return result
        if v.startswith('[') and v.endswith(']'):
            inner = v[1:-1]
            if not inner.strip():
                return []
            return [self._convert_value(x.strip()) for x in inner.split(',') if x.strip()]
        # field_id is always a string identifier
        if key == 'field_id':
            return v
        try:
            return int(v.replace(',', ''))
        except ValueError:
            try:
                return float(v.replace(',', ''))
            except ValueError:
                return v
    
    def _split_dict_items(self, s: str):
        items = []
        current = ""
        depth = 0
        in_quote = False
        quote_char = None
        
        for ch in s:
            if ch in '"\'':
                if not in_quote:
                    in_quote = True
                    quote_char = ch
                elif ch == quote_char:
                    in_quote = False
                    quote_char = None
                current += ch
            elif ch == ',' and depth == 0 and not in_quote:
                items.append(current)
                current = ""
            else:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                current += ch
        
        if current.strip():
            items.append(current)
        return items


def ton_to_json(ton_text: str) -> dict:
    parser = TONParser(ton_text)
    return parser.parse()


# ========== 核心填表流程 ==========
def generate_filling_reference(fs_ton_path: str, taxcomp_ton_path: str, schema_ton_path: str, run_dir: str) -> str:
    """Call LLM to generate filling reference from parsed data and schema."""
    
    # Read inputs
    with open(fs_ton_path, 'r', encoding='utf-8') as f:
        fs_ton = f.read()
    with open(taxcomp_ton_path, 'r', encoding='utf-8') as f:
        taxcomp_ton = f.read()
    with open(schema_ton_path, 'r', encoding='utf-8') as f:
        schema_ton = f.read()
    
    # Token control: truncate if too long, keeping critical sections
    MAX_FS_LEN = 200000
    MAX_TAX_LEN = 100000
    MAX_SCHEMA_LEN = 100000
    
    if len(fs_ton) > MAX_FS_LEN:
        # Keep company_info, signature blocks, and key tables
        fs_ton = _smart_truncate_fs(fs_ton, MAX_FS_LEN)
    
    if len(taxcomp_ton) > MAX_TAX_LEN:
        taxcomp_ton = _smart_truncate_taxcomp(taxcomp_ton, MAX_TAX_LEN)
    
    if len(schema_ton) > MAX_SCHEMA_LEN:
        schema_ton = schema_ton[:MAX_SCHEMA_LEN] + "\n... [schema truncated]"
    
    user_content = f"""## SCHEMA (TON)
  ```
{schema_ton}
```

## FINANCIAL STATEMENTS (TON)
```
{fs_ton}
```

## TAX COMPUTATION (TON)
```
{taxcomp_ton}
```

## INSTRUCTIONS
Generate the complete BIR51 filling reference in TON format.
Follow every rule in the system prompt exactly.
Output ONLY TON. No markdown code blocks.
"""
    
    print(f"[FILLING] Calling {TEXT_MODEL}...")
    print(f"  Input size: FS={len(fs_ton)} chars, TaxComp={len(taxcomp_ton)} chars, Schema={len(schema_ton)} chars")
    
    MAX_RETRY = 2
    last_error = None
    for attempt in range(MAX_RETRY + 1):
        try:
            response = client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_FILLING},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.1,
                max_tokens=64000,
                timeout=1800,
            )
            # Save API response for token tracking
            api_path = os.path.join(run_dir, "filling_api_response.json")
            with open(api_path, 'w', encoding='utf-8') as f:
                json.dump(response.model_dump(), f, indent=2, ensure_ascii=False)
            break
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRY:
                wait = 2 ** attempt
                print(f"  [RETRY] Attempt {attempt+1}/{MAX_RETRY} failed: {e}")
                print(f"  Waiting {wait}s...")
                time.sleep(wait)
            else:
                raise last_error
    
    filling_ton = response.choices[0].message.content
    
    # Clean markdown wrappers
    filling_ton = re.sub(r'```ton\s*', '', filling_ton, flags=re.IGNORECASE)
    filling_ton = re.sub(r'```\s*$', '', filling_ton)
    filling_ton = filling_ton.strip()
    
    # Save raw output
    raw_path = os.path.join(run_dir, "filling_reference_raw.ton")
    with open(raw_path, 'w', encoding='utf-8') as f:
        f.write(filling_ton)
    
    return filling_ton


def _smart_truncate_fs(fs_ton: str, max_len: int) -> str:
    """Smart truncation: keep company_info, signature, key tables."""
    lines = fs_ton.split('\n')
    priority_sections = []
    current_section = []
    in_priority = False
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('@company_info'):
            in_priority = True
        elif stripped.startswith('@signature_block'):
            in_priority = True
        elif stripped.startswith('@page'):
            if in_priority and current_section:
                priority_sections.extend(current_section)
            current_section = [line]
            in_priority = False
        elif stripped.startswith('@table'):
            # Check if it's a key table
            if any(kw in current_section[0] if current_section else '' for kw in [
                'Balance Sheet', 'Income Statement', 'Revenue', 'Profit', 'Loss'
            ]):
                in_priority = True
        
        current_section.append(line)
    
    result = '\n'.join(priority_sections)
    if len(result) > max_len:
        result = result[:max_len] + "\n... [truncated]"
    return result


def _smart_truncate_taxcomp(taxcomp_ton: str, max_len: int) -> str:
    """Smart truncation: keep main computation and all schedules."""
    lines = taxcomp_ton.split('\n')
    result = []
    in_section = False

    for line in lines:
        stripped = line.strip()
        # Match @section blocks (replaces old @schedule format)
        if stripped.startswith('@section'):
            in_section = True
        # Keep lines that are part of a schedule section or key computation items
        if in_section or 'Assessable profit' in line or 'Profit per' in line:
            result.append(line)

    result_text = '\n'.join(result)
    if len(result_text) > max_len:
        result_text = result_text[:max_len] + "\n... [truncated]"
    return result_text


# ========== 输出转换 ==========
def ton_to_flat_fields(filling_json: dict) -> list:
    """Flatten TON filling reference to list of fields for Excel/JSON."""
    fields = []
    
    def extract(obj, part_num=""):
        if obj["type"] == "part":
            part_num = str(obj["properties"].get("part_number", ""))
            for child in obj.get("children", []):
                extract(child, part_num)
        elif obj["type"] == "field":
            p = obj["properties"]
            fields.append({
                "Part": part_num,
                "Box No.": p.get("box", ""),
                "Field ID": p.get("field_id", ""),
                "Field Name (EN)": p.get("name_en", ""),
                "Field Name (ZH)": p.get("name_zh", ""),
                "Suggested Value": p.get("suggested_value", ""),
                "Value Type": p.get("value_type", ""),
                "Source": p.get("source", ""),
                "Source Location": p.get("source_location", ""),
                "Evidence": p.get("source_evidence", ""),
                "Confidence": p.get("confidence", ""),
                "Validation": p.get("validation_status", ""),
                "Needs Review": "YES" if p.get("needs_manual_review") else "",
                "Review Reason": p.get("review_reason", ""),
                "Reasoning": p.get("reasoning", ""),
            })
        else:
            for child in obj.get("children", []):
                extract(child, part_num)
    
    for obj in filling_json.get("objects", []):
        if obj["type"] == "filling_reference":
            for child in obj.get("children", []):
                extract(child)
    
    return fields


def generate_excel(fields: list, output_path: str):
    """Generate styled Excel reference document with 3 sheets."""
    
    # Sheet 1: Filling Guide
    df_main = pd.DataFrame(fields)
    cols = ["Part", "Box No.", "Field ID", "Field Name (EN)", "Field Name (ZH)",
            "Suggested Value", "Value Type", "Source", "Source Location", "Evidence",
            "Confidence", "Validation", "Needs Review", "Review Reason", "Reasoning"]
    for c in cols:
        if c not in df_main.columns:
            df_main[c] = ""
    df_main = df_main[cols]
    
    # Sheet 2: Part 9 Checklist
    part9_fields = [f for f in fields if f.get("Part") == "9"]
    df_part9 = pd.DataFrame(part9_fields)
    if df_part9.empty:
        df_part9 = pd.DataFrame({
            "Box No.": list(range(42, 64)),
            "Form": [f"S{i-41}" for i in range(42, 64)],
            "Value": ["No"] * 22,
            "Reasoning": ["Per company rule, supplementary forms are not applicable"] * 22
        })
    
    # Sheet 3: Manual Review Summary
    review_fields = [f for f in fields if f.get("Needs Review") == "YES"]
    df_review = pd.DataFrame(review_fields)
    if df_review.empty:
        df_review = pd.DataFrame(columns=["Part", "Box No.", "Field ID", "Field Name (EN)", "Review Reason", "Required Action"])
    
    # Color styling function
    def color_row(row):
        if row.get("Needs Review") == "YES":
            return ['background-color: #ffcccc'] * len(row)
        if row.get("Confidence") == "low":
            return ['background-color: #ffffcc'] * len(row)
        if row.get("Validation") == "fail":
            return ['background-color: #ff9999'] * len(row)
        return [''] * len(row)
    
    # Fixed column widths (characters)
    col_widths = {
        "Part": 6, "Box No.": 8, "Field ID": 10,
        "Field Name (EN)": 45, "Field Name (ZH)": 30,
        "Suggested Value": 16, "Value Type": 12, "Source": 22,
        "Source Location": 22, "Evidence": 35,
        "Confidence": 12, "Validation": 10, "Needs Review": 13,
        "Review Reason": 40, "Reasoning": 50
    }

    # Write to Excel
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        # Sheet 1（样式着色是锦上添花，缺少 jinja2 时跳过）
        try:
            styled_main = df_main.style.apply(color_row, axis=1)
            styled_main.to_excel(writer, sheet_name='Filling Guide', index=False)
        except Exception as e:
            print(f"[EXCEL] Style apply failed ({e}), writing without styling.")
            df_main.to_excel(writer, sheet_name='Filling Guide', index=False)

        # Set column widths for Sheet 1
        ws1 = writer.sheets['Filling Guide']
        for col_idx, col_name in enumerate(df_main.columns, 1):
            col_letter = chr(64 + col_idx) if col_idx <= 26 else chr(64 + col_idx // 26) + chr(65 + (col_idx - 1) % 26)
            width = col_widths.get(col_name, 15)
            ws1.column_dimensions[col_letter].width = width

        # Sheet 2
        df_part9.to_excel(writer, sheet_name='Part 9 Checklist', index=False)
        ws2 = writer.sheets['Part 9 Checklist']
        for col_idx, col_name in enumerate(df_part9.columns, 1):
            col_letter = chr(64 + col_idx)
            ws2.column_dimensions[col_letter].width = 25

        # Sheet 3
        df_review.to_excel(writer, sheet_name='Manual Review Summary', index=False)
        ws3 = writer.sheets['Manual Review Summary']
        for col_idx, col_name in enumerate(df_review.columns, 1):
            col_letter = chr(64 + col_idx)
            ws3.column_dimensions[col_letter].width = 40
    
    print(f"[EXCEL] Saved to {output_path}")
    return df_main, df_part9, df_review


def generate_summary(fields: list) -> dict:
    """Generate processing summary statistics."""
    return {
        "total_fields": len(fields),
        "filled": sum(1 for f in fields if f.get("Suggested Value") not in ["", None, "0"]),
        "zero_filled": sum(1 for f in fields if f.get("Suggested Value") == "0"),
        "manual_review": sum(1 for f in fields if f.get("Needs Review") == "YES"),
        "low_confidence": sum(1 for f in fields if f.get("Confidence") == "low"),
        "validation_failures": sum(1 for f in fields if f.get("Validation") == "fail"),
        "validation_warnings": sum(1 for f in fields if f.get("Validation") == "warning"),
    }


# ========== 主入口 ==========
def main():
    parser = argparse.ArgumentParser(description='ProfitsPilot Filling Engine')
    parser.add_argument('--fs-ton', required=True, help='Path to fs_parsed.ton')
    parser.add_argument('--taxcomp-ton', required=True, help='Path to taxcomp_parsed.ton')
    parser.add_argument('--schema', required=True, help='Path to tax_return_schema.ton')
    parser.add_argument('--output-dir', default=None, help='Output directory (default: auto-generated)')
    args = parser.parse_args()
    
    # Validate inputs
    for path in [args.fs_ton, args.taxcomp_ton, args.schema]:
        if not os.path.exists(path):
            print(f"[ERROR] File not found: {path}")
            sys.exit(1)
    
    # Setup output directory
    if args.output_dir:
        run_dir = args.output_dir
    else:
        run_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", 
                               datetime.now().strftime("%Y%m%d_%H%M%S") + "_filling")
    os.makedirs(run_dir, exist_ok=True)
    
    print(f"[START] ProfitsPilot Filling Engine")
    print(f"[START] Run directory: {run_dir}")
    print(f"[START] FS: {args.fs_ton}")
    print(f"[START] Tax Comp: {args.taxcomp_ton}")
    print(f"[START] Schema: {args.schema}")
    
    # Stage 2: Generate filling reference
    filling_ton = generate_filling_reference(args.fs_ton, args.taxcomp_ton, args.schema, run_dir)
    
    filling_ton_path = os.path.join(run_dir, "filling_reference.ton")
    with open(filling_ton_path, 'w', encoding='utf-8') as f:
        f.write(filling_ton)
    
    # Stage 3: Convert to JSON
    try:
        filling_json = ton_to_json(filling_ton)
        json_path = os.path.join(run_dir, "filling_reference.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(filling_json, f, indent=2, ensure_ascii=False)
        print(f"[JSON] Saved to {json_path}")
    except Exception as e:
        print(f"[WARNING] JSON conversion failed: {e}")
        filling_json = None
        json_path = None
    
    # Stage 3: Convert to Excel
    if filling_json:
        fields = ton_to_flat_fields(filling_json)
        excel_path = os.path.join(run_dir, "filling_reference.xlsx")
        generate_excel(fields, excel_path)
        
        # Summary
        summary = generate_summary(fields)
        summary_path = os.path.join(run_dir, "_summary.json")
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        print(f"\n{'='*60}")
        print("FILLING ENGINE COMPLETE")
        print(f"{'='*60}")
        print(f"Total fields: {summary['total_fields']}")
        print(f"Filled: {summary['filled']}")
        print(f"Zero-filled: {summary['zero_filled']}")
        print(f"Manual review: {summary['manual_review']}")
        print(f"Low confidence: {summary['low_confidence']}")
        print(f"Validation failures: {summary['validation_failures']}")
        print(f"Validation warnings: {summary['validation_warnings']}")
        print(f"\nOutputs:")
        print(f"  TON:  {filling_ton_path}")
        print(f"  JSON: {json_path or 'FAILED'}")
        print(f"  Excel: {excel_path}")
        print(f"Logs: {run_dir}")

        # Report own token usage
        api_path = os.path.join(run_dir, "filling_api_response.json")
        total_tokens = 0
        if os.path.exists(api_path):
            try:
                with open(api_path) as f:
                    data = json.load(f)
                total_tokens = data.get("usage", {}).get("total_tokens", 0)
            except Exception:
                pass
        print(f"\n[TOKEN_USAGE] {json.dumps({'stage': 'filling_engine', 'total_tokens': total_tokens})}")
    else:
        print(f"\n[ERROR] Could not parse filling reference TON. Check raw output at:")
        print(f"  {os.path.join(run_dir, 'filling_reference_raw.ton')}")


if __name__ == "__main__":
    main()