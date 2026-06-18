import fitz
import base64
import json
import urllib.request
import sys
import os
import re
import time
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Configuration
MODEL = "qwen3.6-flash"
API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
if not API_KEY:
    print("[FATAL] 缺少环境变量 DASHSCOPE_API_KEY", file=sys.stderr)
    sys.exit(1)
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
REQUEST_TIMEOUT = 300
MAX_TOKENS = 4096
BATCH_SIZE = 30
MAX_PARALLEL = 3
MAX_RETRY = 2
DPI = 150  # Lower DPI for inspector — text readability is enough
thinking = True

SYSTEM_PROMPT = """
You are a senior financial-statement quality inspector. You will be shown all pages of a financial report simultaneously. Your task is to perform a comprehensive quality review across the ENTIRE document for visual and semantic errors.

## CORE PRINCIPLE
Check the document with an auditor's sceptical eye. Flag any visual or semantic inconsistency, error, or irregularity you can identify by reading the text and inspecting the layout.

DO NOT check:
- Arithmetic calculations (subtotals, totals, derived figures).
- Cross-page numerical tie-outs (whether the same figure matches across different pages or tables).
- Numerical transcription errors such as extra digits, missing digits, transposed digits, or abnormal number formatting (e.g., "10,0000", "3,73"). These are handled by a downstream system.

Your scope is strictly: company name, period, currency, column headers, page/note sequencing, continuation markers, reference consistency, spelling and grammar of text, directional terminology, and semantic label-to-note correspondence.

## MANDATORY STEP 0 — CONTINUATION MARKER AUDIT
You MUST perform this check BEFORE checking any of the 10 categories below. Do not skip this step.

Procedure:
1. Go through EVERY page and identify the primary heading on each page. Check ALL of the following locations:
   - Running page headers (text at the very top of the page, often repeated across pages)
   - Section titles at the start of the page content
   - Note titles (e.g., "12. Trade and other receivables")
2. Write down the primary heading for each page.
3. Compare the heading of page N with the heading of page N+1 for ALL consecutive pairs.
4. If page N and page N+1 share the SAME or SUBSTANTIALLY SIMILAR primary heading (e.g., both say "3. Summary of significant accounting policies" or both say "DETAILED INCOME STATEMENT"), then page N+1 MUST include "(Continued)" or "(续)" in its heading.
5. Flag EVERY instance where the same heading appears on consecutive pages but the later page lacks "(Continued)" or "(续)".

This is the highest-priority check. Perform it thoroughly before proceeding.

## INSPECTION METHODOLOGY (10 Categories)

### 1. Company Name Consistency
Method: Scan every page for the company name.
Principle: The company name must be IDENTICAL in wording and spacing across all pages. Flag any deviation (e.g., "JIE COMPANY LIMITED" on one page and "JIE STONE LIMITED" on another).

### 2. Reporting Period Consistency
Method: Identify the base reporting period wording from the cover page or main header (e.g., "For the year ended 31 August 2024" or "For the period ended 31 August 2024"). Then scan every page header, footer, and narrative text for time references.
Principle: The core time descriptor ("year" vs "period") is NOT interchangeable. If the base document uses "year", all other references must also use "year". If the base uses "period", all must use "period". Flag any mixed usage (e.g., a page header saying "For the period ended ..." when the cover says "For the year ended ...").

### 3. Currency Symbol Consistency
Method: Check the currency unit stated in page headers, footers, and table headings.
Principle: The same currency must be used throughout the entire document (e.g., "HK Dollars" everywhere, not "US Dollars" on some pages).

### 4. Year / Period Column Headers
Method: Examine table column headers that represent time periods.
Principle: Column headers should be concise and uniform. They should contain only the essential year or period identifier (e.g., "2024", "2023"). Flag redundant or inconsistent wording such as "Year 2024", "Period 2023", "Fiscal Year 2024", or mixed formats across different tables.

### 5. Note Number, Page Continuity & Continuation Markers
Method:
- Verify that note numbers follow a logical sequence without unexplained gaps or duplicates.
- Verify that printed page numbers at the bottom or top of each page form a continuous natural-number sequence.
- Scan adjacent pages for identical section titles, report headers, or note headings. If page N and page N+1 share the same title (e.g., "3. Summary of significant accounting policies" or "DETAILED INCOME STATEMENT"), page N+1 MUST carry a continuation marker such as "(Continued)" or "(续)".
- Check that the casing and formatting of continuation markers are consistent throughout the document (e.g., "(Continued)" vs "(continued)" should not be mixed).
Principle: Notes should be sequential, printed page numbers should be sequential, and ANY multi-page section must explicitly indicate continuation with consistently formatted markers.

### 6. Page & Note Reference Consistency
Method: Find every citation in the document that references a page or a note (e.g., "see page 12", "as described in Note 2", "further explained in Note 5").
Principle:
- Case and formatting must be consistent throughout (e.g., always "Note" not "note" or "NOTE").
- Semantic correspondence: locate the cited note, read its title and first paragraph to understand its topic, then verify that topic matches the subject matter of the citing paragraph. For example, if a paragraph discusses "going concern" but cites a note whose title and content are about "leases", flag the mismatch. Similarly, if the text says "set out in Note 3" but the actual note covering that topic is Note 6, flag the incorrect note number.

### 7. Spelling, Grammar, and Formatting
Method: Read all narrative text, headings, table labels, and footnotes line by line.
Principle:
- Flag obvious spelling errors in English or Chinese.
- Flag grammatical errors including subject-verb disagreement, missing auxiliary verbs, incorrect tense, missing prepositions, and missing articles.
- Flag formatting inconsistencies such as "Note5" vs "Note 5", inconsistent indentation, or mixed typographic styles.
- Do NOT flag numerical figures for digit errors, extra zeros, missing commas, or abnormal formatting (e.g., "10,0000", "3,73"). These are numerical transcription issues handled by another system.

### 8. Directional Terminology Verification
Method: For paired directional labels such as "(Increase)/decrease" or "used in/(generated from)", verify the following:
1. Determine the account type:
   - Assets (e.g., receivables, inventory): an increase in the asset reduces operating cash → negative figure.
   - Liabilities (e.g., payables, accruals): an increase in the liability increases operating cash → positive figure.
2. In paired notation "(A)/B" or "A/(B)", the term inside parentheses MUST correspond to the scenario that produces a NEGATIVE figure.
   - Example: Receivables are assets. An increase in receivables reduces cash → negative. Label must be "(Increase)/decrease".
   - Example: Payables are liabilities. An increase in payables increases cash → positive. Label must be "Increase/(decrease)", NOT "(Increase)/decrease".
   - Example: "used in" means cash outflow → negative. "generated from" means cash inflow → positive. If the net cash figure is negative, the label must be "(used in)/generated from".
Principle: The parenthesized term must always correspond to the direction that produces a negative cash impact. Flag any mismatch between the label structure and the figure's sign.

### 9. Main Statement Note References → Actual Notes
Method: On primary financial statements, identify note reference numbers attached to line items (e.g., via superscripts or parenthetical citations like "Note 5").
Principle: The cited note number must actually exist in the document, and the line item's subject matter must correspond to that note's actual topic.

### 10. Main Statement Labels → Note Titles Correspondence
Method: Compare line-item labels on primary statements with the titles of their corresponding notes.
Principle: The correspondence is semantic, not literal.
- Acceptable: "Trade receivables" ↔ "Trade and Other Receivables"
- Flag: "Trade receivables" ↔ "Property, Plant and Equipment"
- Flag: "Sales of goods" ↔ "Sales of stones" (core meaning mismatch)
Flag any label whose core meaning does not match its note title.

## OUTPUT RULES
- List ONLY problems found. If the document is clean, output exactly: NO_ISSUES
- Be specific: cite page number, location (top/middle/bottom), and exact text.
- Do NOT invent or assume errors. If unsure, skip.
- Output language: bilingual (English + Chinese) where possible, or match the document language.

## OUTPUT FORMAT
Return a plain-text list. One issue per line.
Use the **exact English category name** from the list below (NOT "Category N"):

1. Company Name Consistency
2. Reporting Period Consistency
3. Currency Symbol Consistency
4. Year / Period Column Headers
5. Note Number, Page Continuity & Continuation Markers
6. Page & Note Reference Consistency
7. Spelling, Grammar, and Formatting
8. Directional Terminology Verification
9. Main Statement Note References → Actual Notes
10. Main Statement Labels → Note Titles Correspondence

```
[Page X] | [Category Name] | [Location] | [Issue description]
```

**Page number rule (CRITICAL):**
`[Page X]` must be the **PRINTED page number** as shown in the page header or footer of the image.
- If the page has a printed number (e.g., "Page 5" at the bottom), use `[Page 5]`.
- If the page has no printed number (e.g., cover page, unnumbered separator), use a descriptive label such as `[Cover Page]`, `[Page N (unnumbered)]`, or `[Unnumbered Page]`.
- NEVER use the batch index or image sequence number as the page number.

Example:
```
[Page 5] | Spelling, Grammar, and Formatting | Middle | "Amount due to a immediate holding company" should be "an immediate".
```

No markdown explanations, no JSON, no summaries — just the list.
"""


def call_api(image_contents, batch_start, batch_end, doc_len, run_dir):
    """Call VLM for a single batch of pages."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": image_contents + [
                    {
                        "type": "text",
                        "text": (
                            f"Inspect pages {batch_start + 1} to {batch_end} "
                            f"out of {doc_len} total pages. "
                            f"IMPORTANT: The numbers {batch_start + 1}-{batch_end} are ONLY internal batch indices, NOT page numbers. "
                            f"When citing page numbers in your output, you MUST use the PRINTED page number visible in the image header/footer, NOT these indices. "
                            f"Return ONLY the issue list (one per line) or 'NO_ISSUES'."
                        )
                    }
                ]
            }
        ],
        # "max_tokens": MAX_TOKENS
        "enable_thinking": thinking
    }

    req_data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(API_URL, data=req_data, method='POST')
    req.add_header('Authorization', f'Bearer {API_KEY}')
    req.add_header('Content-Type', 'application/json')

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            result = json.loads(response.read().decode('utf-8'))

            # Save API response
            api_path = os.path.join(run_dir, f"inspect_{batch_start + 1:03d}_{batch_end:03d}_api.json")
            with open(api_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content']

                # Save raw
                raw_path = os.path.join(run_dir, f"inspect_{batch_start + 1:03d}_{batch_end:03d}_raw.txt")
                with open(raw_path, 'w', encoding='utf-8') as f:
                    f.write(content)

                # Parse issues
                CATEGORY_MAP = {
                    '1': 'Company Name Consistency',
                    '2': 'Reporting Period Consistency',
                    '3': 'Currency Symbol Consistency',
                    '4': 'Year / Period Column Headers',
                    '5': 'Note Number, Page Continuity & Continuation Markers',
                    '6': 'Page & Note Reference Consistency',
                    '7': 'Spelling, Grammar, and Formatting',
                    '8': 'Directional Terminology Verification',
                    '9': 'Main Statement Note References \u2192 Actual Notes',
                    '10': 'Main Statement Labels \u2192 Note Titles Correspondence',
                }
                issues = []
                for line in content.strip().split('\n'):
                    line = line.strip()
                    if not line or line.upper() == 'NO_ISSUES':
                        continue
                    if line.startswith('[Page'):
                        # Replace legacy [Category N] or Category N with English name
                        for num, name in CATEGORY_MAP.items():
                            line = line.replace(f'[Category {num}]', f'[{name}]')
                            line = line.replace(f'Category {num}', name)
                        issues.append(line)

                return {"status": "ok", "issues": issues, "raw": content}

            else:
                error_msg = result.get('error', {}).get('message', 'Unknown error')
                return {"status": "api_error", "error": error_msg}

    except Exception as e:
        return {"status": "exception", "error": str(e)}


def call_api_with_retry(image_contents, batch_start, batch_end, doc_len, run_dir):
    """Call VLM with retry logic."""
    last_result = None
    for attempt in range(MAX_RETRY + 1):
        result = call_api(image_contents, batch_start, batch_end, doc_len, run_dir)
        last_result = result
        if result['status'] == 'ok':
            return {'success': True, 'issues': result['issues'], 'raw': result['raw'],
                    'batch_start': batch_start, 'batch_end': batch_end}

        if attempt < MAX_RETRY:
            wait_time = 2 ** attempt
            print(f"     Batch {batch_start + 1}-{batch_end} failed ({result['status']}: {result['error']}), retrying in {wait_time}s... ({attempt + 1}/{MAX_RETRY})")
            time.sleep(wait_time)

    print(f"     Batch {batch_start + 1}-{batch_end}: FAILED after {MAX_RETRY} retries — {last_result['error']}")
    return {'success': False, 'error': last_result['error'],
            'batch_start': batch_start, 'batch_end': batch_end}


def inspect_pdf(pdf_path, output_file):
    if not API_KEY:
        print("ERROR: API_KEY is not set.")
        return

    # Create timestamped log directory (with milliseconds + random suffix for concurrency safety)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{datetime.now().microsecond // 1000:03d}" + f"_{os.urandom(2).hex()}"
    run_dir = os.path.join(LOG_DIR, f"inspector_{run_id}")
    os.makedirs(run_dir, exist_ok=True)

    print(f"[START] Inspector (BATCH mode, {BATCH_SIZE} pages/batch, {DPI} DPI): {pdf_path}")
    print(f"[LOGS]  Logs will be saved to: {run_dir}")

    # Step 1: Load images — either from a directory or render from PDF
    if os.path.isdir(pdf_path):
        # Image directory mode
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
        # PDF mode: render pages via fitz
        doc = fitz.open(pdf_path)
        doc_len = len(doc)
        print(f"  -> Rendering {doc_len} pages to images at {DPI} DPI...")
        pages_data = []
        zoom = DPI / 72
        for i in range(doc_len):
            page = doc[i]
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            img_path = os.path.join(run_dir, f"page_{i + 1:03d}_image.jpg")
            pix.save(img_path)
            with open(img_path, 'rb') as f:
                img_bytes = f.read()

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

    all_issues = []
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
                all_issues.extend(result['issues'])
                issue_count = len(result['issues'])
                print(f"     Batch {result['batch_start'] + 1}-{result['batch_end']}: OK ({issue_count} issues)")
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
    for api_file in _glob.glob(os.path.join(run_dir, "inspect_*_api.json")):
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
        "inspector_version": "1.0",
        "pdf_path": pdf_path,
        "total_pages": doc_len,
        "dpi": DPI,
        "model": MODEL,
        "success": len(batch_errors) == 0,
        "error": "; ".join(batch_errors) if batch_errors else None,
        "total_issues": len(all_issues),
        "issues": all_issues,
        "_token_usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        },
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Step 4: Summary
    summary = {
        "run_id": run_id,
        "pdf_path": pdf_path,
        "output_file": output_file,
        "total_pages": doc_len,
        "total_batches": total_batches,
        "batch_size": BATCH_SIZE,
        "total_issues": len(all_issues),
        "batch_errors": batch_errors,
        "model": MODEL,
        "dpi": DPI,
        "timestamp": datetime.now().isoformat()
    }
    summary_path = os.path.join(run_dir, "_summary.json")
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\n[SUCCESS] Inspection saved to {output_file}")
    print(f"[LOGS]    Run summary saved to {summary_path}")
    print(f"Total pages: {doc_len}, Total issues: {len(all_issues)}, Failed batches: {len(batch_errors)}")
    if all_issues:
        print(f"\nSample issues:")
        for issue in all_issues[:5]:
            print(f"  - {issue}")
        if len(all_issues) > 5:
            print(f"  ... and {len(all_issues) - 5} more")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python inspector.py <pdf_path> [output_file]")
        print("  output_file: optional, defaults to 'inspect_output.json'")
    else:
        pdf_path = sys.argv[1]
        output_file = sys.argv[2] if len(sys.argv) > 2 else "inspect_output.json"
        inspect_pdf(pdf_path, output_file)
