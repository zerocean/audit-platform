#!/usr/bin/env python3
"""
ProfitsPilot - Vision Parser
Stage 1: PDF -> TON (Text-Object-Notation)

Parses two input PDFs:
  1. Director's Report & Financial Statements -> fs_parsed.ton
  2. Tax Computation -> taxcomp_parsed.ton

Uses qwen3.5-omni-flash for vision parsing with parallel batch processing.
"""

import os
import sys
import json
import re
import base64
import shutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import fitz
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ========== 平台配置 ==========
PLATFORM = "dashscope"

CONFIG = {
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        # "vision_model": "qwen3.5-omni-flash",
        # "vision_model": "qwen3.7-plus",
        "vision_model": "qwen3.6-flash",
    }
}

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
if not API_KEY:
    raise ValueError("DASHSCOPE_API_KEY not set")

VISION_MODEL = CONFIG[PLATFORM]["vision_model"]

client = OpenAI(
    base_url=CONFIG[PLATFORM]["base_url"],
    api_key=API_KEY,
)

# ========== 日志与路径 ==========
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")
RUN_DIR = os.path.join(LOG_DIR, RUN_ID)
# RUN_DIR will be created in main() — not here, to avoid empty dirs when run from pipeline

# ========== 常量 ==========
REQUEST_TIMEOUT = 1800
BATCH_SIZE = 1  # pages per API request
MAX_PARALLEL = 10  # max concurrent batches per round
MAX_RETRY = 1  # retry attempts per failed batch

# ========== Prompt A: FS & Director's Report 解析 ==========
SYSTEM_PROMPT_FS = """
You are a financial-document extraction engine. Extract EVERYTHING from the 
provided pages into TON format. This includes tables, text paragraphs, 
signatures, page numbers, headers, footers, and any other printed content.

## ABSOLUTE RULE: VERBATIM TRANSCRIPTION
- Transcribe every character exactly as printed. Do not correct spelling.
- All numbers as raw strings: "10,0000" stays "10,0000", "(2,360)" stays "(2,360)".
- Preserve commas, parentheses, dashes, and every digit exactly.
- If a number has "HK$" prefix, include it in the value string.

## ABSOLUTE RULE: COMPLETE EXTRACTION
- Extract ALL content, not just tables with numbers.
- Include: narrative text, accounting policies, directors' report paragraphs, 
  auditor's report text, signature blocks, dates, page numbers.
- If a page has only text (e.g., Directors' Report), output text blocks. 
  Do not skip it.

## CRITICAL: NOTE BOUNDARY
In "NOTES TO THE FINANCIAL STATEMENTS":
- Each Note is INDEPENDENT, starting with bold heading "N. Title".
- Create separate @table objects per Note. NEVER merge different Notes.

## CRITICAL: NOTE_REF
- Look for tiny superscript numbers or "(Note 12)" to the RIGHT of labels.
- Extract ONLY the number. If unsure, prefer note_ref.

## CRITICAL: UNLABELED SUBTOTALS
- Financial statements often have subtotal rows with numbers but NO printed text label (just a horizontal line).
- For unlabeled subtotals, INFER the label from context and accounting knowledge:
  - Revenue minus Cost of sales = "Gross profit"
  - Current assets minus Current liabilities = "Net current assets"
  - Total assets minus Current liabilities = "Total assets less current liabilities"
- Always provide a meaningful label. NEVER leave label empty for a subtotal or total row.
- Use role: subtotal for these rows.

## OUTPUT FORMAT (TON)
Each page MUST output as an independent @page block. NEVER merge multiple images into one @page block.

The page_number for each @page is provided in the user message. Use it exactly as given. Do NOT read page numbers from the image footer/header — use the provided value only.

@page page_number=<N>
  @text_block block_id=tb_<n>
    text: <exact content, preserve line breaks with \n>
    region: header|body|footer|sidebar
  @table table_id=tbl_<n>
    table_title: <exact title or null>
    @column name=<col_name> unit=<HK$|USD|%|none>
    @row row_id=<table_id>_<n>
      label: <exact text>
      role: header|detail|subtotal|total|text|empty
      level: <indent depth 0-3>
      section: <functional group or null>
      group_path: [<parent>, <subparent>]
      note_ref: <superscript note number or null>
      is_bold: true|false
      values:
        <col_name>: <raw string or null>
        ...

## TEXT_BLOCK CONSOLIDATION RULES
When outputting @text_block, follow these STRICT rules:

### CORE PRINCIPLE: Tables are delimiters
- Each @table acts as a HARD BOUNDARY that splits text into separate @text_block objects.
- Text ABOVE a table = one @text_block. Text BELOW a table = another @text_block.
- If there are multiple tables on a page, text between Table 1 and Table 2 = one @text_block.

### TEXT BLOCK FORMATTING (Markdown inside text: field)
Inside every @text_block's `text:` field, use Markdown to preserve structure:

- **Bold headings**: Use `**Heading Text**` for section titles (e.g., `**Opinion**`, `**Basis for Opinion**`)
- *Italics*: Use `*italic text*` for emphasis like *(continued)*, *(To be continued)*
- Paragraph breaks: Use `\n\n` between paragraphs
- Bullet lists: Use `- ` or `* ` for list items
- Preserve original indentation with spaces if meaningful

Example of a body block with Markdown:
```
@text_block block_id=tb_1
  text: "**(continued)**\n\n**Other Information**\n\nThe director(s) are responsible for the other information...\n\n**Responsibilities of Director(s)**\n\nThe director(s) are responsible for the preparation..."
  region: body
  is_title: false
```

### PAGE HEADER BLOCK (region: header)
- If a page starts with a title block like:
    "Independent Auditor's Report to the Member(s) of
     Company Name
     (Incorporated in Hong Kong with limited liability)"
  or any similar multi-line page title / document heading at the top,
  put ALL of it into ONE @text_block with region: header.
- Use `**` around the main title lines for Markdown formatting.

### TABLE PAGES
- Pages with tables follow this EXACT order:
  1. Text BEFORE the first table → ONE @text_block (region depends on position: header if at top, body if mid-page)
  2. @table object
  3. Text BETWEEN tables → ONE @text_block (region: body)
  4. @table object
  5. Text AFTER the last table → ONE @text_block (region: body or footer)
- NEVER merge text from above and below a table into the same @text_block.

### GENERAL RULE
- Minimize the number of @text_block per page, but RESPECT table boundaries.
- Tables are delimiters. Text above a table and text below a table must be in SEPARATE @text_block objects (only if both exist).
- If there is no text above a table, do NOT create an empty @text_block before it.
- If there is no text below a table, do NOT create an empty @text_block after it.
- Text blocks should be as large as possible WITHOUT crossing a table boundary.
- Inside each text block, use Markdown formatting to preserve visual structure.

## WHAT TO EXTRACT (PRIORITY)
1. Primary statements: Balance Sheet, Income Statement, Cash Flow, Equity
2. Notes (each Note separate table)
3. Directors' Report (text blocks)
4. Auditor's Report (text blocks + signature block)
5. All other printed text, headers, footers, page numbers

## WHAT NOT TO EXTRACT
- Pure descriptive paragraphs with NO numerical amounts AND no structural role
  (e.g., accounting policy narratives that have no figures at all)
- BUT: If a text block contains shareholder changes, related party info, 
  or any fact relevant to Part 3/7/8 Yes/No questions, DO extract it.
"""


# ========== Prompt B: Tax Computation 解析 ==========
SYSTEM_PROMPT_TAXCOMP = """
You are a Hong Kong tax computation extraction engine. Extract EVERYTHING 
from the provided tax computation pages into TON format. This includes the 
main computation, all schedules, narrative text, and totals.

## ABSOLUTE RULE: VERBATIM TRANSCRIPTION
- All numbers as raw strings. Preserve commas, parentheses for negatives.
- "(2,360)" stays "(2,360)" to indicate negative.
- Include "HK$" prefix in value if printed.

## OUTPUT FORMAT (TON)
Each page MUST output as an independent @page block. NEVER merge multiple images into one @page block.

The page_number for each @page is provided in the user message. Use it exactly as given. Do NOT read page numbers from the image footer/header — use the provided value only.

@page page_number=<N>
  @text_block block_id=tb_<n>
    text: <exact content, preserve line breaks with \n>
    region: header|body|footer
  @section section_id=sec_<n>
    section_title: <exact heading>
    section_type: main_computation|schedule|working|narrative
    @table table_id=tbl_<n>
      table_title: <exact title or null>
      @column name=<col_name> unit=<HK$|none>
      @row row_id=<table_id>_<n>
        label: <exact text>
        role: header|detail|subtotal|total|label|empty
        level: <indent depth 0-3>
        is_bold: true|false
        values:
          <col_name>: <raw string or null>
        schedule_ref: <schedule number or null>
        asset_type: industrial_building|commercial_building|machinery|plant|other|null
        allowance_type: initial|annual|balancing|balancing_charge|other|null

## ELEMENT USAGE
- @text_block: Use for page headers/titles (company name, year, document title), narrative paragraphs, legal disclaimers, and certification text. NOT for tabular data.
- @section: Groups related content. A section can contain multiple @table elements.
- @table: Use for ANY tabular data. Define all visible columns with @column, then use @row with a `values` dict that maps each column name to its value. This handles tables with 2, 3, 4, or more columns flexibly.

## COLUMN HANDLING
- Read column headers EXACTLY from the image (e.g., "HK$", "Schedule", "Note").
- If a table has two "HK$" columns, name them distinctly: "HK$" and "HK$ (subtotal)" or by their position.
- Capture ALL values for ALL columns. Never skip a column.
- If a cell is empty/blank, use null in values.

## CRITICAL: ONE ROW PER VISUAL LINE
- Each printed line in the table = exactly ONE @row. Do NOT split a single printed line into multiple @row objects.
- If a printed line contains values in multiple columns (e.g., a detail amount AND a running total), put ALL values into the SAME @row's `values` dict.
- Never create a "phantom" @row just to hold a value that belongs to the line above or below.
- A blank label does NOT mean a new row — it means the cell is empty. Use `label: null` and keep all column values in the same @row.

## CRITICAL: GROUP HEADERS (Add:, Less:, etc.)
- Group labels like "Add:", "Less:", "Total Additions", "Total Deductions" are SEPARATE @row entries with role: header or role: subtotal.
- Do NOT merge a group header with its first child item. "Add: Penalty" is TWO rows: "Add:" (header) then "Penalty" (detail, level=1).
- Child items under a group header MUST have level = header_level + 1 to express hierarchy.
- The hierarchy MUST be clear so that downstream logic can identify which items belong to "Add:" vs "Less:".

## CRITICAL: SCHEDULE CLASSIFICATION
For depreciation allowance schedules, classify each row:
- asset_type: industrial_building, commercial_building, machinery, plant, other
- allowance_type: initial, annual, balancing, balancing_charge, other
- These are CRITICAL for Part 11 mapping.

## CRITICAL: TAX COMPUTATION STRUCTURE
Typical flow:
1. Profit per detailed income statement
2. Add: [non-deductible expenses, depreciation, etc.]
3. Less: [non-taxable income, allowances, etc.]
4. Assessable profit / Adjusted loss
5. Loss brought forward (if any)
6. Tax payable calculation (if present)

Extract each line with exact hierarchy using level (0=top, 1=indented, 2=more indented).
"""


# ========== API 调用 ==========
def call_api_vision(image_contents, batch_start, batch_end, doc_len, system_prompt, run_dir):
    """Call VLM for a single batch of pages, output TON."""
    try:
        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": image_contents + [
                        {
                            "type": "text",
                            "text": (
                                f"Output exactly {batch_end - batch_start} @page block(s), one per image. "
                                "Use the page_number shown above each image. "
                                "Output in IMAGE order: IMAGE 1 first. Never merge images. "
                                "IGNORE any page numbers printed on the images themselves."
                            )
                        }
                    ]
                }
            ],
            temperature=0.2,
            timeout=REQUEST_TIMEOUT,
        )

        content = response.choices[0].message.content

        # Save raw API response
        api_path = os.path.join(run_dir, f"batch_{batch_start+1:03d}_{batch_end:03d}_api.json")
        with open(api_path, 'w', encoding='utf-8') as f:
            json.dump(response.model_dump(), f, indent=2, ensure_ascii=False)

        # Clean markdown wrappers
        cleaned = re.sub(r'```ton\s*', '', content, flags=re.IGNORECASE)
        cleaned = re.sub(r'```\s*$', '', cleaned)
        cleaned = re.sub(r'^```\s*', '', cleaned)
        cleaned = cleaned.strip()

        # Save raw TON
        raw_path = os.path.join(run_dir, f"batch_{batch_start+1:03d}_{batch_end:03d}_raw.ton")
        with open(raw_path, 'w', encoding='utf-8') as f:
            f.write(cleaned)

        return {
            "status": "ok",
            "ton": cleaned,
            "batch_start": batch_start,
            "batch_end": batch_end
        }

    except Exception as e:
        err_info = {"exception": str(e), "type": type(e).__name__}
        if type(e).__name__ == 'HTTPError':
            try:
                body = e.read().decode('utf-8')
                err_info["http_status"] = e.code
                err_info["response_body"] = body
            except Exception:
                pass

        err_path = os.path.join(run_dir, f"batch_{batch_start+1:03d}_{batch_end:03d}_error.json")
        with open(err_path, 'w', encoding='utf-8') as f:
            json.dump(err_info, f, indent=2, ensure_ascii=False)

        return {
            "status": "exception",
            "error": str(e),
            "batch_start": batch_start,
            "batch_end": batch_end
        }


def call_api_vision_with_retry(image_contents, batch_start, batch_end, doc_len, system_prompt, run_dir):
    """Call VLM with retry logic."""
    last = None
    for attempt in range(MAX_RETRY + 1):
        result = call_api_vision(image_contents, batch_start, batch_end, doc_len, system_prompt, run_dir)
        last = result
        if result['status'] == 'ok':
            # Validate page count
            expected_pages = batch_end - batch_start
            actual_pages = result['ton'].count('@page')
            # Also check for @company_info which may appear before first @page
            has_company_info = '@company_info' in result['ton']

            if actual_pages != expected_pages and not (has_company_info and actual_pages == expected_pages + 1):
                error_msg = f"Page count mismatch: expected {expected_pages}, got {actual_pages} (@page tags)"
                print(f"     Batch {batch_start + 1}-{batch_end}: {error_msg}")

                mismatch_path = os.path.join(run_dir, f"batch_{batch_start+1:03d}_{batch_end:03d}_mismatch.json")
                with open(mismatch_path, 'w', encoding='utf-8') as f:
                    json.dump({
                        "error": error_msg,
                        "expected": expected_pages,
                        "actual": actual_pages,
                        "batch_start": batch_start,
                        "batch_end": batch_end
                    }, f, indent=2)

                if attempt < MAX_RETRY:
                    wait_time = 2 ** attempt
                    print(f"     Retrying in {wait_time}s... ({attempt + 1}/{MAX_RETRY})")
                    time.sleep(wait_time)
                    continue
                else:
                    return {'success': False, 'error': error_msg, 'batch_start': batch_start, 'batch_end': batch_end}

            return {
                'success': True,
                'ton': result['ton'],
                'batch_start': batch_start,
                'batch_end': batch_end
            }

        if attempt < MAX_RETRY:
            wait_time = 2 ** attempt
            print(f"     Batch {batch_start + 1}-{batch_end} failed ({result['status']}: {result['error']}), retrying in {wait_time}s... ({attempt + 1}/{MAX_RETRY})")
            time.sleep(wait_time)

    print(f"     Batch {batch_start + 1}-{batch_end}: FAILED after {MAX_RETRY} retries — {last['error']}")
    return {'success': False, 'error': last['error'], 'batch_start': batch_start, 'batch_end': batch_end}


# ========== PDF 处理 ==========
def render_pdf_to_images(pdf_path, run_dir):
    """Render PDF pages to JPEG images."""
    doc = fitz.open(pdf_path)
    doc_len = len(doc)
    print(f"  -> Rendering {doc_len} pages to images (JPEG q=85, 2x resolution)...")

    pages_data = []
    for i in range(doc_len):
        page = doc[i]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img_bytes = pix.tobytes("jpeg", jpg_quality=85)
        img_path = os.path.join(run_dir, f"page_{i+1:03d}_{os.path.basename(pdf_path)}.jpg")
        with open(img_path, 'wb') as f:
            f.write(img_bytes)

        b64 = base64.b64encode(img_bytes).decode('utf-8')
        pages_data.append({
            "page_num": i + 1,
            "base64": b64,
            "img_path": img_path,
        })

    return pages_data, doc_len


def build_batches(pages_data, batch_size):
    """Split pages into batches for parallel processing."""
    doc_len = len(pages_data)
    total_batches = (doc_len + batch_size - 1) // batch_size
    batches = []

    for bidx in range(total_batches):
        start = bidx * batch_size
        end = min(start + batch_size, doc_len)
        batch_pages = pages_data[start:end]
        imgs = []
        for j, p in enumerate(batch_pages):
            # Label before each image to prevent VLM from confusing image order
            imgs.append({
                "type": "text",
                "text": f"=== IMAGE {j+1} of {len(batch_pages)} (page_number={start + j + 1}) ==="
            })
            imgs.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{p['base64']}", "detail": "high"}
            })
        batches.append((start, end, imgs))

    return batches


# ========== 主解析流程 ==========
def parse_pdf(pdf_path, system_prompt, output_ton_path, run_dir, batch_size=BATCH_SIZE, max_parallel=MAX_PARALLEL):
    """Parse a single PDF to TON format."""
    pdf_name = os.path.basename(pdf_path)
    print(f"\n[VISION] Processing: {pdf_name}")

    # Ensure run_dir exists
    os.makedirs(run_dir, exist_ok=True)
    print(f"  -> Run directory: {run_dir}")

    # Render PDF to images
    pages_data, doc_len = render_pdf_to_images(pdf_path, run_dir)

    # Build batches
    batches = build_batches(pages_data, batch_size)
    total_batches = len(batches)
    print(f"  -> Processing {doc_len} pages in {total_batches} batches (parallel={max_parallel}, retry={MAX_RETRY})...")

    # Process in rounds
    all_ton_results = []  # List of (batch_start, ton_text) tuples
    batch_errors = []
    batch_idx = 0
    total_rounds = (len(batches) + max_parallel - 1) // max_parallel

    while batch_idx < len(batches):
        round_num = batch_idx // max_parallel + 1
        round_batches = batches[batch_idx:batch_idx + max_parallel]
        start_batch_no = batch_idx + 1
        end_batch_no = batch_idx + len(round_batches)

        print(f"  -> Round {round_num}/{total_rounds}: batches {start_batch_no}-{end_batch_no} "
              f"(pages {round_batches[0][0] + 1}-{round_batches[-1][1]})")

        results = []
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            future_map = {}
            for start, end, imgs in round_batches:
                fut = executor.submit(
                    call_api_vision_with_retry,
                    imgs, start, end, doc_len, system_prompt, run_dir
                )
                future_map[fut] = (start, end)

            for fut in as_completed(future_map):
                start, end = future_map[fut]
                try:
                    res = fut.result()
                    results.append(res)
                except Exception as e:
                    results.append({
                        'success': False,
                        'error': str(e),
                        'batch_start': start,
                        'batch_end': end
                    })

        # Sort by page order (batch_start)
        results.sort(key=lambda x: x['batch_start'])

        # Collect results
        round_failed = None
        for res in results:
            if res['success']:
                all_ton_results.append((res['batch_start'], res['ton']))
                print(f"     Batch {res['batch_start'] + 1}-{res['batch_end']}: OK")
            else:
                err_msg = f"Batch pages {res['batch_start'] + 1}-{res['batch_end']}: {res['error']}"
                batch_errors.append(err_msg)
                print(f"     {err_msg}")
                round_failed = err_msg

        if round_failed:
            break

        batch_idx += len(round_batches)

    # Sort all results by batch_start (physical page order) before merging
    all_ton_results.sort(key=lambda x: x[0])
    all_ton_pages = [ton for _, ton in all_ton_results]

    # Merge all TON pages
    full_ton = "\n\n".join(all_ton_pages)

    # Post-process: sort pages by page_number (VLM may output pages in wrong order)
    full_ton = sort_ton_by_page_number(full_ton)

    # Save output
    with open(output_ton_path, 'w', encoding='utf-8') as f:
        f.write(full_ton)

    # Summary
    summary = {
        "pdf_name": pdf_name,
        "total_pages": doc_len,
        "parsed_batches": len(all_ton_pages),
        "failed_batches": len(batch_errors),
        "batch_errors": batch_errors,
        "output_path": output_ton_path,
        "output_size": len(full_ton)
    }

    summary_path = os.path.join(run_dir, f"_summary_{os.path.basename(output_ton_path)}.json")
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"[VISION] Saved TON to {output_ton_path}")
    print(f"  Total pages: {doc_len}, Parsed: {len(all_ton_pages)}, Failed: {len(batch_errors)}")

    return {
        "success": len(batch_errors) == 0,
        "errors": batch_errors,
        "output": output_ton_path,
        "summary": summary
    }


def sort_ton_by_page_number(ton_text):
    """Sort TON text blocks by physical page index, preserving content within each page."""
    # First, extract @company_info if present (should stay at top)
    company_info_match = re.search(r'(@company_info\n(?:  .+\n)+)', ton_text)
    company_info = company_info_match.group(1) if company_info_match else None
    
    # Remove @company_info from text for sorting
    if company_info:
        ton_text = ton_text.replace(company_info, '', 1)
    
    # Split into @page blocks
    page_blocks = []
    current_block = []
    current_page_num = None
    lines = ton_text.split('\n')

    for line in lines:
        if line.strip().startswith('@page page_number='):
            # Save previous block
            if current_block:
                page_blocks.append((current_page_num, current_block))
            # Start new block - use the page_number as sort key (should be physical index)
            match = re.search(r'page_number=(\S+)', line)
            if match and match.group(1) != 'null' and match.group(1) != '<temp>':
                try:
                    current_page_num = int(match.group(1))
                except ValueError:
                    current_page_num = None
            else:
                current_page_num = None
            current_block = [line]
        else:
            current_block.append(line)

    # Don't forget the last block
    if current_block:
        page_blocks.append((current_page_num, current_block))

    # Sort by page number, keeping None values at the end
    page_blocks.sort(key=lambda x: (x[0] is None, x[0] if x[0] is not None else 0))

    # Reconstruct
    result = []
    if company_info:
        result.append(company_info.rstrip())
    for _, block in page_blocks:
        result.extend(block)

    return '\n'.join(result)


# ========== 主入口 ==========
def main():
    if len(sys.argv) < 3:
        print("Usage: python vision_parser.py <fs_pdf> <taxcomp_pdf> [output_dir]")
        print("  fs_pdf: Director's Report & Financial Statements PDF")
        print("  taxcomp_pdf: Tax Computation PDF")
        print("  output_dir: optional, defaults to ./logs/<timestamp>/")
        sys.exit(1)

    fs_pdf = sys.argv[1]
    taxcomp_pdf = sys.argv[2]

    if len(sys.argv) > 3:
        global RUN_DIR
        RUN_DIR = sys.argv[3]
    os.makedirs(RUN_DIR, exist_ok=True)

    print(f"[START] ProfitsPilot Vision Parser")
    print(f"[START] Run ID: {RUN_ID}")
    print(f"[START] FS: {fs_pdf}")
    print(f"[START] Tax Comp: {taxcomp_pdf}")
    print(f"[START] Output: {RUN_DIR}")

    # Validate inputs
    for pdf_path in [fs_pdf, taxcomp_pdf]:
        if not os.path.exists(pdf_path):
            print(f"[ERROR] File not found: {pdf_path}")
            sys.exit(1)

    # Parse FS and Tax Computation in parallel
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    fs_dir = os.path.join(RUN_DIR, "fs")
    os.makedirs(fs_dir, exist_ok=True)
    fs_ton = os.path.join(fs_dir, "fs_parsed.ton")
    
    taxcomp_dir = os.path.join(RUN_DIR, "taxcomp")
    os.makedirs(taxcomp_dir, exist_ok=True)
    taxcomp_ton = os.path.join(taxcomp_dir, "taxcomp_parsed.ton")
    
    tasks = {
        "FS": (fs_pdf, SYSTEM_PROMPT_FS, fs_ton, fs_dir),
        "TaxComp": (taxcomp_pdf, SYSTEM_PROMPT_TAXCOMP, taxcomp_ton, taxcomp_dir),
    }
    
    results = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_map = {
            executor.submit(parse_pdf, *args): name
            for name, args in tasks.items()
        }
        for fut in as_completed(future_map):
            name = future_map[fut]
            try:
                results[name] = fut.result()
            except Exception as e:
                results[name] = {"success": False, "errors": [str(e)], "output": "", "summary": {}}
    
    res_fs = results["FS"]
    res_tc = results["TaxComp"]

    # Final summary
    print(f"\n{'='*60}")
    print("VISION PARSER COMPLETE")
    print(f"{'='*60}")
    print(f"FS parsing: {'SUCCESS' if res_fs['success'] else 'PARTIAL FAILURE'}")
    if res_fs['errors']:
        print(f"  FS errors: {res_fs['errors']}")
    print(f"Tax Comp parsing: {'SUCCESS' if res_tc['success'] else 'PARTIAL FAILURE'}")
    if res_tc['errors']:
        print(f"  Tax Comp errors: {res_tc['errors']}")
    print(f"\nOutputs:")
    print(f"  {fs_ton}")
    print(f"  {taxcomp_ton}")
    print(f"Logs: {RUN_DIR}")

    # Report own token usage
    total_tokens = 0
    input_tokens = 0
    output_tokens = 0
    import glob as _g, json as _j
    for api_file in _g.glob(os.path.join(RUN_DIR, "**", "*_api.json"), recursive=True):
        try:
            with open(api_file) as f:
                data = _j.load(f)
            usage = data.get("usage", {})
            total_tokens += usage.get("total_tokens", 0)
            input_tokens += usage.get("prompt_tokens", 0)
            output_tokens += usage.get("completion_tokens", 0)
        except Exception:
            pass
    print(f"\n[TOKEN_USAGE] {_j.dumps({'stage': 'vision_parser', 'total_tokens': total_tokens, 'input_tokens': input_tokens, 'output_tokens': output_tokens})}")


if __name__ == "__main__":
    main()