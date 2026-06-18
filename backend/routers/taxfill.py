"""TaxFill API"""
import os, json, uuid, tempfile, shutil, time
from datetime import datetime, timezone
from fastapi import APIRouter, UploadFile, File, Depends
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from database import get_db
from shared_db.models import Task, TaskStep, TaskFile
from shared_db import ensure_model_pricing, compute_task_cost
from auth import get_current_user
from services.taxfill_pipeline import run_taxfill_pipeline
from config import TAXFILL_HK_DIR, OUTPUTS_DIR, TRACKING_ENABLED

router = APIRouter(prefix="/api/taxfill", tags=["taxfill"])


@router.post("/pipeline")
async def taxfill_pipeline(
    fs_pdf: UploadFile = File(...),
    taxcomp_pdf: UploadFile = File(...),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not fs_pdf.filename or not taxcomp_pdf.filename:
        return JSONResponse({"success": False, "error": "请上传两个 PDF 文件"}, status_code=400)
    for f in [fs_pdf, taxcomp_pdf]:
        if not f.filename.lower().endswith('.pdf'):
            return JSONResponse({"success": False, "error": f"{f.filename} 不是 PDF"}, status_code=400)

    task = Task(user_id=user.id, tool_type="taxfill", project_name="TaxFill_HK",
                input_filename=f"{fs_pdf.filename} (+1)", input_file_count=2, status="running",
                source="production" if TRACKING_ENABLED else "development")
    db.add(task); db.commit(); db.refresh(task)

    run_id = uuid.uuid4().hex[:12]
    tmp_dir = os.path.join(tempfile.gettempdir(), f"taxfill_{run_id}")
    os.makedirs(tmp_dir, exist_ok=True)

    # Persistent upload dir
    upload_dir = os.path.join(OUTPUTS_DIR, "uploads", str(task.id))
    os.makedirs(upload_dir, exist_ok=True)

    try:
        # Save uploads to both tmp and persistent location
        fs_name = fs_pdf.filename
        taxcomp_name = taxcomp_pdf.filename
        fs_content = await fs_pdf.read()
        taxcomp_content = await taxcomp_pdf.read()

        fs_path = os.path.join(tmp_dir, f"fs_{fs_name}")
        taxcomp_path = os.path.join(tmp_dir, f"taxcomp_{taxcomp_name}")
        with open(fs_path, "wb") as f: f.write(fs_content)
        with open(taxcomp_path, "wb") as f: f.write(taxcomp_content)

        # Persistent copies
        fs_uploaded = os.path.join(upload_dir, fs_name)
        taxcomp_uploaded = os.path.join(upload_dir, taxcomp_name)
        shutil.copy2(fs_path, fs_uploaded)
        shutil.copy2(taxcomp_path, taxcomp_uploaded)

        # Record uploaded files in TaskFile for admin-dashboard
        if TRACKING_ENABLED:
            db.add_all([
                TaskFile(task_id=task.id, file_type="input", file_name=fs_name,
                         file_size=len(fs_content), oss_url=fs_uploaded),
                TaskFile(task_id=task.id, file_type="input", file_name=taxcomp_name,
                         file_size=len(taxcomp_content), oss_url=taxcomp_uploaded),
            ])
            db.commit()

        result = await run_taxfill_pipeline(fs_path, taxcomp_path)
        pipeline_run_id = result["run_id"]

        # Collect all file info
        files = {
            "uploaded": [
                {"name": fs_name, "type": "fs_pdf"},
                {"name": taxcomp_name, "type": "taxcomp_pdf"},
            ],
            "parser_output": [
                {"name": "fs_parsed.ton", "type": "fs_ton"},
                {"name": "taxcomp_parsed.ton", "type": "taxcomp_ton"},
            ],
            "output": [
                {"name": "filling_reference.xlsx", "type": "excel"},
                {"name": "filling_reference.json", "type": "json"},
                {"name": "filling_reference.ton", "type": "ton"},
            ]
        }

        task.status = "success"
        if result.get("token_usage") and TRACKING_ENABLED:
            task.total_tokens = result["token_usage"]
            ensure_model_pricing("dashscope", "qwen3.6-flash")
            ensure_model_pricing("dashscope", "deepseek-v4-flash")
            # Create TaskSteps with per-model input/output token counts
            vis_tokens = result.get("vision_tokens", 0)
            vis_input = result.get("vision_input", 0) or vis_tokens
            vis_output = result.get("vision_output", 0)
            fill_tokens = result.get("filling_tokens", 0)
            fill_input = result.get("filling_input", 0)
            fill_output = result.get("filling_output", 0) or fill_tokens
            # Fallback: if no split data, estimate conservatively
            if not vis_tokens and not fill_tokens:
                vis_tokens = int(task.total_tokens * 0.2)
                fill_tokens = task.total_tokens - vis_tokens
                vis_input = vis_tokens
                fill_output = fill_tokens
            vin, vout, vis_cost = compute_task_cost(vis_input, vis_output, "dashscope", "qwen3.6-flash")
            fin, fout, fill_cost = compute_task_cost(fill_input, fill_output, "dashscope", "deepseek-v4-flash")
            task.total_cost = vis_cost + fill_cost
            steps = []
            if vis_tokens:
                steps.append(TaskStep(task_id=task.id, step_type="vision_parser", status="success",
                         model_name="qwen3.6-flash", model_provider="dashscope",
                         total_tokens=vis_tokens, input_tokens=vis_input, output_tokens=vis_output,
                         input_cost=vin, output_cost=vout, total_cost=vis_cost))
            if fill_tokens:
                steps.append(TaskStep(task_id=task.id, step_type="filling_engine", status="success",
                         model_name="deepseek-v4-flash", model_provider="dashscope",
                         total_tokens=fill_tokens, input_tokens=fill_input, output_tokens=fill_output,
                         input_cost=fin, output_cost=fout, total_cost=fill_cost))
            if steps:
                db.add_all(steps)
        task.result_json = json.dumps({
            "run_id": pipeline_run_id,
            "files": files,
            "filling_json": result.get("filling_json"),
        }, ensure_ascii=False, default=str)
        task.completed_at = datetime.now(timezone.utc)

        # Record output files in TaskFile
        if TRACKING_ENABLED:
            output_files = []
            excel_path = result.get("excel_path")
            json_path = result.get("json_path")
            if excel_path and os.path.exists(excel_path):
                output_files.append(TaskFile(task_id=task.id, file_type="output",
                    file_name="filling_reference.xlsx",
                    file_size=os.path.getsize(excel_path), oss_url=excel_path))
            if json_path and os.path.exists(json_path):
                output_files.append(TaskFile(task_id=task.id, file_type="output",
                    file_name="filling_reference.json",
                    file_size=os.path.getsize(json_path), oss_url=json_path))
            if output_files:
                db.add_all(output_files)

        db.commit()

        return {
            "success": True, "task_id": task.id,
            "run_id": pipeline_run_id,
            "filling_json": result.get("filling_json"),
            "files": files,
        }
    except Exception as e:
        task.status = "failed"; task.error_message = str(e); db.commit()
        return JSONResponse({"success": False, "task_id": task.id, "error": str(e)}, status_code=500)
    finally:
        try: shutil.rmtree(tmp_dir)
        except Exception: pass


@router.get("/download/{run_id}/{filename}")
async def taxfill_download(run_id: str, filename: str):
    base = os.path.join(TAXFILL_HK_DIR, "logs", run_id, "filling")
    file_path = os.path.join(base, filename)
    if not os.path.exists(file_path):
        return JSONResponse({"success": False, "error": "文件不存在"}, status_code=404)
    return FileResponse(file_path, filename=filename)


@router.get("/download-upload/{task_id}/{filename}")
async def taxfill_download_upload(task_id: int, filename: str):
    base = os.path.join(OUTPUTS_DIR, "uploads", str(task_id))
    file_path = os.path.join(base, filename)
    if not os.path.exists(file_path):
        return JSONResponse({"success": False, "error": "文件不存在"}, status_code=404)
    return FileResponse(file_path, filename=filename)
