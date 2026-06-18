import fitz
import base64
import json
import sys
import os
import re
import time
import shutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

# ========== 平台配置（只改这一行即可切换）==========
PLATFORM = "dashscope"        # 可选: "doubao" | "dashscope" | "siliconflow"
# ===================================================

CONFIG = {
    "doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-pro-260215",
    },
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.5-omni-flash",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/deepseek-vl2",
    },
}

cfg = CONFIG[PLATFORM]
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")   # 所有平台共用同一个环境变量
MODEL = cfg["model"]

client = OpenAI(
    base_url=cfg["base_url"],
    api_key=API_KEY,
)

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
REQUEST_TIMEOUT = 1800
MAX_TOKENS = 131072 # 128k
BATCH_SIZE = 1  # pages per API request
MAX_PARALLEL = 10  # max concurrent batches per round
MAX_RETRY = 2     # retry attempts per failed batch

SYSTEM_PROMPT = """
You are a financial-document OCR engine. Your ONLY job is to transcribe every table on the provided pages into structured JSON.

You will receive multiple page images in ORDER (first image = the first page of this batch, second = the next page, etc.). Process ALL pages in this batch and return a SINGLE JSON object.

## ABSOLUTE RULE: STRING TRANSCRIPTION
- Treat every number as a **raw string** — never as a numeric value.
- If the original shows "10,0000", output "10,0000". If it shows "667,496", output "667,496". Never "fix", round, reformat, or correct anything.
- Preserve commas, parentheses for negatives, dashes for zero, and every digit exactly as printed.
- Even if a number is mathematically impossible (e.g., 10,0000), you must keep it verbatim.

## ABSOLUTE RULE: NO INVENTED ROWS
- If a row is NOT physically printed in the original image, do NOT output it.
- NEVER calculate, infer, or add subtotals, totals, or any derived figures that do not exist as visible rows in the table.
- The "subtotal" role is ONLY for rows that are explicitly printed with a label (e.g., "Total", "Subtotal", "Carrying amount"). If the image has no such row, do not create one with an empty label.

## OUTPUT FORMAT
Return ONLY valid JSON. No markdown code blocks, no explanations.

```json
{
  "pages": [
    {
      "page_number": <printed page number from image header/footer or null>,
      "tables": [
        {
          "table_name": "exact title as printed",
          "is_note": true,
          "note_number": "6",
          "columns": ["col1", "col2", ...],
          "is_continuation": false,
          "continued_from": null,
          "rows": [
            {
              "row_id": "<table_name>_<index>",
              "label": "text exactly as printed",
              "role": "header|detail|subtotal|calculated|text",
              "level": 0,
              "section": "group name or null",
              "group_path": ["Group", "Subgroup"],
              "note_ref": "note number or null",
              "values": {"<column>": "raw string or null"}
            }
          ]
        }
      ]
    }
  ]
}
```

## FIELD DEFINITIONS
- **page_number**: The page number AS PRINTED on the image (usually in header or footer). Read it from the visual page, NOT from the batch index. If the image has no printed page number (e.g., cover page), use `null`. NEVER invent a number.
- **role**:
  - `header` = bold section title (no numbers)
  - `detail` = line item with figures
  - `subtotal` = any total row (group subtotal or statement total)
  - `calculated` = derived figure from cross-group arithmetic (e.g., gross profit = revenue − cost)
  - `text` = non-tabular text that must be preserved
- **level**: Indentation depth (0 = top-level header, 1 = indented detail, 2 = sub-sub-item). Infer from visual indentation.
- **section**: The functional group this row belongs to (e.g., "revenue", "cost_of_sales", "selling_expenses").
- **group_path**: Array of increasingly specific group names from outer to inner. Example: `["Administrative expenses", "Staff costs"]`.
- **note_ref**: CRITICAL — Look for small superscript numbers or text like "Note 6", "(Note 12)" to the RIGHT of the row label or near the figure. These are often tiny. Extract ONLY the number (e.g., "6", "12"). If absent, use null.
- **is_continuation**: Set `true` if the table title contains "(Continued)" or "(续)". Otherwise `false`.
- **continued_from**: If `is_continuation` is true, put the original table name here (strip "(Continued)"). Otherwise null.
- **is_note**: Set `true` if the table belongs to the "NOTES TO THE FINANCIAL STATEMENTS" section (page header contains this text). Primary financial statements (Balance Sheet, Income Statement, etc.) are `false`.
- **note_number**: The Note number to which this table belongs. Extract from the section heading like "6. Revenue..." → `"6"`, "9. Staff costs" → `"9"`. Only set when `is_note=true` and a number is present; otherwise `null`.

## CROSS-PAGE TABLES
If a table title says "DETAILED INCOME STATEMENT (Continued)":
1. Keep the title exact in `table_name`.
2. Set `is_continuation: true` and `continued_from: "DETAILED INCOME STATEMENT"`.
3. Do NOT merge with previous pages — each page is independent in the output array. The downstream system will handle merging if needed.

## NOTE_REF EXTRACTION
Pay extreme attention to small superscript numbers or parenthetical note references:
- Example: `Revenue` with a tiny `6` to its upper-right → `"note_ref": "6"`
- Example: `Trade receivables (Note 12)` → `"note_ref": "12"`
- If you are unsure whether a small number is a note_ref or part of the amount, prefer extracting it as `note_ref`.

## NOTE_NUMBER vs NOTE_REF (CRITICAL DISTINCTION)
- **note_number** (table-level): Identifies which Note this table *belongs to*. Extracted from the section heading (e.g., "6. Revenue..." → "6", "10. Taxation" → "10"). Only present when `is_note=true`.
- **note_ref** (row-level): A reference *to another Note* found within a row label (e.g., superscript 6, or "(Note 12)"). This is a cross-reference, not the table's own identity.

## NOTE BOUNDARY DETECTION (CRITICAL — READ THIS CAREFULLY)
In "NOTES TO THE FINANCIAL STATEMENTS", each **Note** is an INDEPENDENT chapter covering a different accounting topic.
- Note 6 is about Revenue. Note 7 is about Other expenses. They are UNRELATED and must NEVER share the same table.
- A Note is identified by its **heading**: a bold line in the format **"N. Title"** (e.g., "6. Revenue, other revenue and gains", "7. Other expenses", "10. Taxation").
- This heading marks the START of a new, independent table. Even if two Notes appear on the SAME PAGE, create SEPARATE table objects.

### Correct vs Incorrect:
❌ WRONG: One table containing both Note 6 rows and Note 7 rows.
✅ CORRECT: 
   - Table 1: note_number="6", table_name="Revenue, other revenue and gains", rows=[...Note 6 content...]
   - Table 2: note_number="7", table_name="Other expenses", rows=[...Note 7 content...]

## is_note, note_number and table_name
- `is_note`: Set `true` ONLY for tables that belong to a specific Note within the "NOTES TO THE FINANCIAL STATEMENTS" section.
- `note_number`: Extract from the Note heading (the "N." prefix). Examples: "6. Revenue..." → "6", "7. Other expenses" → "7", "10. Taxation" → "10".
- `table_name`: Use the Note heading text WITHOUT the number prefix (e.g., "Revenue, other revenue and gains"). The Note heading is the bold title at the start of each Note, NOT the first row label inside the table.

Special cases:
- A Note may span multiple pages → all pages share the same `note_number`.
- A single Note may contain multiple sub-tables → they all share the same `note_number`.
- If no number is visible, set `is_note: true` and `note_number: null`.
- Primary financial statements (Balance Sheet, Income Statement, etc.) are NOT Notes → `is_note: false`, `note_number: null`.

## MULTI-TABLE PAGES
If a page contains multiple distinct tables — including multiple Notes on the same page — put EACH in the `tables` array as a separate object.
If no financial tables exist on a page, return `{"page_number": null, "tables": []}` for that page.

## WHAT NOT TO EXTRACT (APPLIES EVERYWHERE)
Do NOT include pure descriptive paragraphs as rows in the tables array. This rule applies to ALL parts of the report — Notes, primary financial statements, auditors' report, directors' report, and any other section.

Examples of content to EXCLUDE from rows:
- Accounting policy descriptions
- Narrative explanations of transactions or events
- Disclosures that contain no numerical amounts (no figures in any column)
- Introductory or concluding paragraphs within any section
- Descriptions of relationships, parties, or controlling interests that have no amounts

Only extract rows that represent actual financial data: line items with amounts, subtotals, totals, or section headers that organize numerical rows.

## EMPTY TABLE RULE
If a section (including any Note) contains only descriptive text with no tabular data and no numerical amounts in ANY row, do NOT create a table object for it at all. The `tables` array on that page should simply omit it — do not output an empty-table placeholder.
"""


def call_api(image_contents, batch_start, batch_end, doc_len, run_dir):
    """Call VLM for a single batch of pages."""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": image_contents + [
                        {
                            "type": "text",
                            "text": (
                                f"Extract all tables from pages {batch_start + 1} to {batch_end} "
                                f"(inclusive) out of {doc_len} total pages. "
                                f"Return a JSON object with a top-level 'pages' array. "
                                f"The first image is PDF page index {batch_start + 1}, "
                                f"the second is index {batch_start + 2}, and so on. "
                                f"Remember: page_number must be the PRINTED page number from the image, not this index."
                            )
                        }
                    ]
                }
            ],
            temperature=0.3,
            #  max_tokens=MAX_TOKENS,
            timeout=REQUEST_TIMEOUT,
        )

        content = response.choices[0].message.content

        # Save API response
        api_path = os.path.join(run_dir, f"batch_{batch_start + 1:03d}_{batch_end:03d}_api.json")
        with open(api_path, 'w', encoding='utf-8') as f:
            json.dump(response.model_dump(), f, indent=2, ensure_ascii=False)

        # Save raw
        raw_path = os.path.join(run_dir, f"batch_{batch_start + 1:03d}_{batch_end:03d}_raw.txt")
        with open(raw_path, 'w', encoding='utf-8') as f:
            f.write(content)

        # Clean JSON: remove markdown code blocks AND thinking tags
        cleaned = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        cleaned = re.sub(r'^```json\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
        cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)
            if 'pages' not in parsed:
                raise ValueError("Missing top-level 'pages' array")

            # Ensure page_number: if VLM omitted it, assume no printed number
            for p in parsed['pages']:
                if 'page_number' not in p:
                    p['page_number'] = None

            # Save parsed
            parsed_path = os.path.join(run_dir, f"batch_{batch_start + 1:03d}_{batch_end:03d}_parsed.json")
            with open(parsed_path, 'w', encoding='utf-8') as f:
                json.dump(parsed, f, indent=2, ensure_ascii=False)

            return {"status": "ok", "pages": parsed['pages']}

        except (json.JSONDecodeError, ValueError) as e:
            err_path = os.path.join(run_dir, f"batch_{batch_start + 1:03d}_{batch_end:03d}_error.json")
            with open(err_path, 'w', encoding='utf-8') as f:
                json.dump({"error": str(e), "cleaned_preview": cleaned[:5000]}, f, indent=2)
            return {"status": "json_error", "error": str(e)}

    except Exception as e:
        err_info = {"exception": str(e), "type": type(e).__name__}
        # Try to read HTTPError response body for detailed API error message
        if type(e).__name__ == 'HTTPError':
            try:
                body = e.read().decode('utf-8')
                err_info["http_status"] = e.code
                err_info["response_body"] = body
            except Exception:
                pass
        err_path = os.path.join(run_dir, f"batch_{batch_start + 1:03d}_{batch_end:03d}_error.json")
        with open(err_path, 'w', encoding='utf-8') as f:
            json.dump(err_info, f, indent=2, ensure_ascii=False)
        return {"status": "exception", "error": str(e)}


def call_api_with_retry(image_contents, batch_start, batch_end, doc_len, run_dir):
    """Call VLM with retry logic.
    
    Special handling for rate-limit errors (429/RateLimitError/insufficient_quota):
    - These bypass MAX_RETRY and get unlimited retries with 60s fixed wait
    - Only non-rate-limit errors are subject to MAX_RETRY limit
    """
    last_result = None
    rate_limit_attempts = 0
    max_rate_limit_retries = 30  # safety cap to prevent infinite loops
    
    attempt = 0
    while True:
        result = call_api(image_contents, batch_start, batch_end, doc_len, run_dir)
        last_result = result
        
        if result['status'] == 'ok':
            # CRITICAL: Validate page count matches batch size
            expected_pages = batch_end - batch_start
            actual_pages = len(result['pages'])
            if actual_pages != expected_pages:
                error_msg = f"Page count mismatch: expected {expected_pages}, got {actual_pages}"
                print(f"     Batch {batch_start + 1}-{batch_end}: {error_msg}")
                # Log the mismatch for debugging
                mismatch_path = os.path.join(run_dir, f"batch_{batch_start + 1:03d}_{batch_end:03d}_mismatch.json")
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
                    # All retries exhausted, return as failure
                    return {'success': False, 'error': error_msg, 'batch_start': batch_start, 'batch_end': batch_end}
            
            return {'success': True, 'pages': result['pages'], 'batch_start': batch_start, 'batch_end': batch_end}

        # Check if this is a rate-limit error
        error_str = str(result.get('error', '')).lower()
        is_rate_limit = (
            result.get('status') == 'exception' and 
            ('ratelimit' in error_str or 
             '429' in error_str or 
             'insufficient_quota' in error_str or
             'quota' in error_str)
        )
        
        if is_rate_limit:
            rate_limit_attempts += 1
            if rate_limit_attempts > max_rate_limit_retries:
                print(f"     Batch {batch_start + 1}-{batch_end}: FAILED after {max_rate_limit_retries} rate-limit retries")
                return {'success': False, 'error': last_result['error'], 'batch_start': batch_start, 'batch_end': batch_end}
            
            wait_time = 60  # fixed 60s wait for rate limit
            print(f"     Batch {batch_start + 1}-{batch_end} rate limited (attempt {rate_limit_attempts}/{max_rate_limit_retries}), waiting {wait_time}s...")
            time.sleep(wait_time)
            # Rate-limit retries don't count against MAX_RETRY, just loop again
            continue
        
        if attempt < MAX_RETRY:
            wait_time = 2 ** attempt
            print(f"     Batch {batch_start + 1}-{batch_end} failed ({result['status']}: {result['error']}), retrying in {wait_time}s... ({attempt + 1}/{MAX_RETRY})")
            time.sleep(wait_time)
            attempt += 1
        else:
            print(f"     Batch {batch_start + 1}-{batch_end}: FAILED after {MAX_RETRY} retries — {last_result['error']}")
            return {'success': False, 'error': last_result['error'], 'batch_start': batch_start, 'batch_end': batch_end}

def extract_page_numbers_from_pdf(doc):
    """Extract printed page numbers from PDF headers/footers using regex.
    Only matches formats like 'Page 3', 'page 3', 'PAGE 3' surrounded by whitespace."""
    page_number_map = {}
    pattern = re.compile(r'(?i)(?<!\S)page\s+(\d+)(?!\S)')
    for i in range(len(doc)):
        page = doc[i]
        rect = page.rect
        # Search header (top 10%) and footer (bottom 10%)
        header_rect = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.1)
        footer_rect = fitz.Rect(rect.x0, rect.y1 - rect.height * 0.1, rect.x1, rect.y1)
        header_text = page.get_text("text", clip=header_rect)
        footer_text = page.get_text("text", clip=footer_rect)
        combined_text = header_text + "\n" + footer_text
        match = pattern.search(combined_text)
        if match:
            page_number_map[i] = int(match.group(1))
    return page_number_map


def extract_vision(pdf_path, output_file):
    if not API_KEY:
        print("ERROR: API_KEY is not set.")
        return

    # Create timestamped log directory
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(LOG_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)

    print(f"[START] Vision Parsing (BATCH mode, {BATCH_SIZE} pages/batch): {pdf_path}")
    print(f"[LOGS]  Logs will be saved to: {run_dir}")
    
    page_number_map = {}  # Populated in PDF mode via regex scanning

    # Step 1: Load images — either from a directory or render from PDF
    if os.path.isdir(pdf_path):
        # Image directory mode (e.g. Word -> images via LibreOffice)
        image_files = sorted([
            f for f in os.listdir(pdf_path)
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.tiff'))
        ])
        doc_len = len(image_files)
        print(f"  -> Loading {doc_len} images from directory...")
        pages_data = []
        for i, img_name in enumerate(image_files):
            src_path = os.path.join(pdf_path, img_name)
            with open(src_path, 'rb') as f:
                img_bytes = f.read()
            img_path = os.path.join(run_dir, f"page_{i + 1:03d}_image.jpg")
            shutil.copy(src_path, img_path)
            img_base64 = base64.b64encode(img_bytes).decode('utf-8')
            pages_data.append({
                "page_num": i + 1,
                "base64": img_base64,
                "img_path": img_path
            })
    else:
        # PDF mode: render pages via fitz, compress to JPEG to reduce payload size
        doc = fitz.open(pdf_path)
        doc_len = len(doc)
        print(f"  -> Rendering {doc_len} pages to images (JPEG q=85)...")
        
        # Pre-scan printed page numbers from headers/footers
        print(f"  -> Scanning printed page numbers from PDF text...")
        page_number_map = extract_page_numbers_from_pdf(doc)
        if page_number_map:
            print(f"  -> Found printed page numbers for {len(page_number_map)} pages")
        
        pages_data = []
        for i in range(doc_len):
            page = doc[i]
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))
            img_bytes = pix.tobytes("jpeg", jpg_quality=85)
            img_path = os.path.join(run_dir, f"page_{i + 1:03d}_image.jpg")
            with open(img_path, 'wb') as f:
                f.write(img_bytes)

            img_base64 = base64.b64encode(img_bytes).decode('utf-8')
            pages_data.append({
                "page_num": i + 1,
                "base64": img_base64,
                "img_path": img_path
            })

    # Step 2: Process in rounds (MAX_PARALLEL batches per round)
    total_batches = (doc_len + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"  -> Processing {doc_len} pages in {total_batches} batches (parallel={MAX_PARALLEL}, retry={MAX_RETRY})...")

    # Pre-build all batches
    batches = []
    for batch_idx in range(total_batches):
        batch_start = batch_idx * BATCH_SIZE
        batch_end = min(batch_start + BATCH_SIZE, doc_len)
        batch_pages = pages_data[batch_start:batch_end]
        image_contents = []
        for p in batch_pages:
            image_contents.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{p['base64']}",
                    "detail": "high"
                }
            })
        batches.append((batch_start, batch_end, image_contents))

    all_pages = []
    batch_errors = []
    total_rounds = (len(batches) + MAX_PARALLEL - 1) // MAX_PARALLEL
    batch_idx = 0
    round_num = 0

    while batch_idx < len(batches):
        round_num += 1
        round_batches = batches[batch_idx:batch_idx + MAX_PARALLEL]
        start_batch_no = batch_idx + 1
        end_batch_no = batch_idx + len(round_batches)
        print(f"  -> Round {round_num}/{total_rounds}: batches {start_batch_no}-{end_batch_no} (pages {round_batches[0][0] + 1}-{round_batches[-1][1]})")

        results = []
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            future_to_batch = {}
            for batch_start, batch_end, image_contents in round_batches:
                future = executor.submit(call_api_with_retry, image_contents, batch_start, batch_end, doc_len, run_dir)
                future_to_batch[future] = (batch_start, batch_end)

            for future in as_completed(future_to_batch):
                batch_start, batch_end = future_to_batch[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    print(f"     Batch {batch_start + 1}-{batch_end}: UNEXPECTED ERROR — {e}")
                    results.append({'success': False, 'error': str(e), 'batch_start': batch_start, 'batch_end': batch_end})

        # Sort by page order before collecting
        results.sort(key=lambda r: r['batch_start'])

        # Collect successes first, then check for failures
        round_failed = None
        for result in results:
            if result['success']:
                # CRITICAL: Override VLM-returned page_number with regex-extracted printed page number
                # to prevent VLM hallucinations and ensure accuracy
                for i, p in enumerate(result['pages']):
                    physical_idx = result['batch_start'] + i  # 0-based physical page index
                    if physical_idx in page_number_map:
                        p['page_number'] = page_number_map[physical_idx]
                all_pages.extend(result['pages'])
                print(f"     Batch {result['batch_start'] + 1}-{result['batch_end']}: OK ({len(result['pages'])} pages)")
            else:
                err_msg = f"Batch pages {result['batch_start'] + 1}-{result['batch_end']}: {result['error']}"
                batch_errors.append(err_msg)
                print(f"     {err_msg}")
                round_failed = err_msg

        if round_failed:
            break

        batch_idx += len(round_batches)

    # Step 3: Aggregate tokens, then save final output
    total_tokens = 0
    input_tokens = 0
    output_tokens = 0
    import glob as _glob
    for api_file in _glob.glob(os.path.join(run_dir, "*_api.json")):
        try:
            with open(api_file) as f:
                data = json.load(f)
            usage = data.get("usage", {})
            total_tokens += usage.get("total_tokens", 0)
            input_tokens += usage.get("prompt_tokens", 0)
            output_tokens += usage.get("completion_tokens", 0)
        except Exception:
            pass

    output = {
        "pages": all_pages,
        "success": len(batch_errors) == 0,
        "error": "; ".join(batch_errors) if batch_errors else None,
        "_token_usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        },
        "_model": MODEL,
    }
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Step 4: Summary
    summary = {
        "run_id": run_id,
        "pdf_path": pdf_path,
        "output_file": output_file,
        "total_pages": doc_len,
        "parsed_pages": len(all_pages),
        "failed_pages": doc_len - len(all_pages),
        "total_batches": total_batches,
        "batch_size": BATCH_SIZE,
        "batch_errors": batch_errors,
        "total_tables": sum(len(p.get('tables', [])) for p in all_pages),
        "pages_with_tables": sum(1 for p in all_pages if len(p.get('tables', [])) > 0),
        "empty_table_pages": [
            {"page_number": p['page_number'], "tables": 0}
            for p in all_pages if len(p.get('tables', [])) == 0
        ],
        "model": MODEL,
        "timestamp": datetime.now().isoformat()
    }
    summary_path = os.path.join(run_dir, "_summary.json")
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\n[SUCCESS] Parsed data saved to {output_file}")
    print(f"[LOGS]    Run summary saved to {summary_path}")
    print(f"Total pages: {len(all_pages)}/{doc_len}, Total tables: {summary['total_tables']}")
    if summary['empty_table_pages']:
        empty_nums = [p['page_number'] for p in summary['empty_table_pages']]
        print(f"[WARNING] Pages with empty tables: {empty_nums}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parser_vision_json_old.py <pdf_path> [output_file]")
        print("  output_file: optional, defaults to 'parse_output_vision.json'")
    else:
        pdf_path = sys.argv[1]
        output_file = sys.argv[2] if len(sys.argv) > 2 else "parse_output_vision.json"
        extract_vision(pdf_path, output_file)
