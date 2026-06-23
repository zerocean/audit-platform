"""Audit API — 创建任务 + 异步执行 + SSE 流"""
import os, json, uuid, tempfile, shutil, re
from datetime import datetime, timezone
from fastapi import APIRouter, UploadFile, File, Depends, Form
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db, SessionLocal
from shared_db.models import Task, TaskStep, TaskFile
from shared_db import ensure_model_pricing, compute_task_cost
from auth import get_current_user
from services.pdf_utils import is_pdf_file
from services.audit_parser import run_vision_parser, run_inspector
from services.audit_llm import run_audit_stream, run_audit_sync
from config import OUTPUTS_DIR, AUDIT_MODEL, VISION_MODEL, INSPECTOR_MODEL, TRACKING_ENABLED

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditJSONReq(BaseModel):
    data: dict
    filename: str = "data.json"
    task_id: int | None = None


@router.post("/pdf")
async def audit_pdf(file: UploadFile = File(...),
                     source: str = Form("frontend"),
                     user=Depends(get_current_user),
                     db: Session = Depends(get_db)):
    if not is_pdf_file(file.filename):
        return JSONResponse({"success": False, "error": "仅支持 PDF 文件"}, status_code=400)

    content = await file.read()
    if len(content) / (1024 * 1024) > 50:
        return JSONResponse({"success": False, "error": "文件过大，限 50MB"}, status_code=400)

    task_source = source if TRACKING_ENABLED else "development"
    task = Task(user_id=user.id, tool_type="audit", project_name="audit-report-review",
                input_filename=file.filename, status="running", source=task_source)
    db.add(task); db.commit(); db.refresh(task)

    # Save uploaded file — OSS or local
    from services.oss import upload_or_local
    upload_dir = os.path.join(OUTPUTS_DIR, "uploads", str(task.id))
    os.makedirs(upload_dir, exist_ok=True)
    uploaded_path = os.path.join(upload_dir, file.filename)
    with open(uploaded_path, "wb") as f: f.write(content)
    oss_url = upload_or_local(uploaded_path, "audit", task.id, file.filename)

    # Record file in TaskFile for admin-dashboard
    if TRACKING_ENABLED:
        db.add(TaskFile(task_id=task.id, file_type="input", file_name=file.filename,
                         file_size=len(content), oss_url=oss_url))
        db.commit()

    run_id = uuid.uuid4().hex[:12]
    tmp_dir = os.path.join(tempfile.gettempdir(), f"audit_{run_id}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        ext = os.path.splitext(file.filename)[1] or ".pdf"
        input_path = os.path.join(tmp_dir, f"input{ext}")
        with open(input_path, "wb") as f: f.write(content)

        result = await run_vision_parser(input_path)
        data = result["data"]
        vis_tokens = result.get("token_usage", 0)
        vis_input = result.get("token_input", vis_tokens)
        vis_output = result.get("token_output", 0)
        vis_model = result.get("model") or VISION_MODEL

        if TRACKING_ENABLED:
            ensure_model_pricing("dashscope", vis_model)
            in_cost, out_cost, vis_cost = compute_task_cost(vis_input, vis_output, "dashscope", vis_model)
            task.total_tokens = vis_tokens
            task.total_cost = vis_cost
            db.add(TaskStep(
                task_id=task.id, step_type="vision_parser", status="success",
                model_name=vis_model, model_provider="dashscope",
                total_tokens=vis_tokens, input_tokens=vis_input, output_tokens=vis_output,
                input_cost=in_cost, output_cost=out_cost, total_cost=vis_cost,
            ))
            db.commit()

        task.status = "parsed"
        task.result_json = json.dumps({
            "parsed_json": data, "pages": len(data.get("pages", [])),
            "uploaded_file": file.filename,
        }, ensure_ascii=False)
        db.commit()

        return {
            "success": True, "task_id": task.id,
            "parsed_json": data, "pages": len(data.get("pages", [])),
            "parser_success": data.get("success", False),
            "error": data.get("error"),
        }
    except Exception as e:
        task.status = "failed"; task.error_message = str(e); db.commit()
        return JSONResponse({"success": False, "task_id": task.id, "error": str(e)}, status_code=500)
    finally:
        try: shutil.rmtree(tmp_dir)
        except Exception: pass


@router.post("/json")
async def audit_json(req: AuditJSONReq):
    return StreamingResponse(
        _audit_sse_with_save(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


async def _audit_sse_with_save(req: AuditJSONReq):
    full_text = ""
    usage_data = None
    async for chunk in run_audit_stream(req.data, req.filename):
        yield chunk
        if chunk.startswith("data: [USAGE] "):
            try: usage_data = json.loads(chunk[14:].strip())
            except Exception: pass
            continue
        if chunk.startswith("data: ") and not chunk.startswith("data: [") and chunk != "data: [DONE]\n\n":
            full_text += chunk[6:].replace("⏎", "\n")

    if req.task_id:
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == req.task_id).first()
            if task:
                # Merge with existing result
                existing = {}
                if task.result_json:
                    try: existing = json.loads(task.result_json)
                    except Exception: pass
                existing["audit_text"] = full_text
                matches = list(re.finditer(r'<table>([\s\S]*?)</table>', full_text, re.IGNORECASE))
                if matches:
                    existing["audit_table"] = matches[-1][1].strip()
                task.result_json = json.dumps(existing, ensure_ascii=False)
                task.status = "audited"  # audit LLM done, inspector may still be running
                if usage_data:
                    llm_tokens = usage_data.get("total_tokens", 0)
                    llm_input = usage_data.get("prompt_tokens", 0)
                    llm_output = usage_data.get("completion_tokens", 0)
                    if TRACKING_ENABLED:
                        task.total_tokens = (task.total_tokens or 0) + llm_tokens
                        ensure_model_pricing("dashscope", AUDIT_MODEL)
                        in_cost, out_cost, llm_cost = compute_task_cost(llm_input, llm_output, "dashscope", AUDIT_MODEL)
                        task.total_cost = float(task.total_cost or 0) + llm_cost
                        step = TaskStep(
                            task_id=task.id,
                            step_type="audit_llm",
                            status="success",
                            model_name=AUDIT_MODEL,
                            model_provider="dashscope",
                            total_tokens=llm_tokens,
                            input_tokens=llm_input,
                            output_tokens=llm_output,
                            input_cost=in_cost,
                            output_cost=out_cost,
                            total_cost=llm_cost,
                        )
                        db.add(step)
                db.commit()
                # Check if inspector already finished — if so, mark fully complete
                insp_done = db.query(TaskStep).filter(
                    TaskStep.task_id == task.id,
                    TaskStep.step_type == "inspector",
                    TaskStep.status == "success",
                ).first()
                if insp_done:
                    task.status = "success"
                    task.completed_at = datetime.now(timezone.utc)
                    db.commit()
        finally:
            db.close()


@router.post("/json/sync")
async def audit_json_sync(req: AuditJSONReq, user=Depends(get_current_user)):
    """同步审计 — 供 email_worker 等后台服务调用"""
    text, usage = await run_audit_sync(req.data, req.filename)

    if req.task_id and TRACKING_ENABLED:
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == req.task_id).first()
            if task:
                existing = {}
                if task.result_json:
                    try: existing = json.loads(task.result_json)
                    except Exception: pass
                existing["audit_text"] = text
                matches = list(re.finditer(r'<table>([\s\S]*?)</table>', text, re.IGNORECASE))
                if matches:
                    existing["audit_table"] = matches[-1][1].strip()
                task.result_json = json.dumps(existing, ensure_ascii=False)
                task.status = "audited"  # sync path: audit done, inspector may follow separately
                if usage:
                    llm_tokens = usage.get("total_tokens", 0)
                    llm_input = usage.get("prompt_tokens", 0)
                    llm_output = usage.get("completion_tokens", 0)
                    task.total_tokens = (task.total_tokens or 0) + llm_tokens
                    ensure_model_pricing("dashscope", AUDIT_MODEL)
                    in_cost, out_cost, llm_cost = compute_task_cost(llm_input, llm_output, "dashscope", AUDIT_MODEL)
                    task.total_cost = float(task.total_cost or 0) + llm_cost
                    db.add(TaskStep(
                        task_id=task.id, step_type="audit_llm", status="success",
                        model_name=AUDIT_MODEL, model_provider="dashscope",
                        total_tokens=llm_tokens,
                        input_tokens=llm_input,
                        output_tokens=llm_output,
                        input_cost=in_cost, output_cost=out_cost, total_cost=llm_cost,
                    ))
                task.completed_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()

    return {"success": True, "report": text, "usage": usage}


@router.post("/inspector")
async def audit_inspector(
    file: UploadFile = File(...),
    task_id: int = Form(None),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_pdf_file(file.filename):
        return JSONResponse({"success": False, "error": "仅支持 PDF 文件"}, status_code=400)

    content = await file.read()
    if len(content) / (1024 * 1024) > 50:
        return JSONResponse({"success": False, "error": "文件过大，限 50MB"}, status_code=400)

    run_id = uuid.uuid4().hex[:12]
    tmp_dir = os.path.join(tempfile.gettempdir(), f"insp_{run_id}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        ext = os.path.splitext(file.filename)[1] or ".pdf"
        input_path = os.path.join(tmp_dir, f"input{ext}")
        with open(input_path, "wb") as f: f.write(content)

        result = await run_inspector(input_path)
        insp_tokens = result.get("token_usage", 0)
        insp_input = result.get("_token_input", insp_tokens)
        insp_output = result.get("_token_output", 0)
        insp_model = result.get("_model") or result.get("model") or INSPECTOR_MODEL

        # Save TaskStep if task_id provided
        if task_id:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task and TRACKING_ENABLED:
                ensure_model_pricing("dashscope", insp_model)
                in_cost, out_cost, insp_cost = compute_task_cost(insp_input, insp_output, "dashscope", insp_model)
                task.total_tokens = (task.total_tokens or 0) + insp_tokens
                task.total_cost = float(task.total_cost or 0) + insp_cost
                db.add(TaskStep(
                    task_id=task.id, step_type="inspector", status="success",
                    model_name=insp_model, model_provider="dashscope",
                    total_tokens=insp_tokens, input_tokens=insp_input, output_tokens=insp_output,
                    input_cost=in_cost, output_cost=out_cost, total_cost=insp_cost,
                ))
                # Merge inspector results into result_json
                existing = {}
                if task.result_json:
                    try: existing = json.loads(task.result_json)
                    except Exception: pass
                existing["inspector"] = {
                    "total_pages": result.get("total_pages", 0),
                    "total_issues": result.get("total_issues", 0),
                    "issues": result.get("issues", []),
                }
                task.result_json = json.dumps(existing, ensure_ascii=False)
                db.commit()

                # If audit LLM already finished (status="audited"), mark task complete
                task = db.query(Task).filter(Task.id == task.id).first()  # refresh
                if task and task.status == "audited":
                    task.status = "success"
                    task.completed_at = datetime.now(timezone.utc)
                    db.commit()

        return {"success": True, "total_pages": result.get("total_pages", 0),
                "total_issues": result.get("total_issues", 0),
                "issues": result.get("issues", []),
                "model": INSPECTOR_MODEL}
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    finally:
        try: shutil.rmtree(tmp_dir)
        except Exception: pass


@router.get("/download-upload/{task_id}")
async def audit_download_upload(task_id: int):
    """Download the uploaded PDF for an audit task (OSS or local)"""
    from services.oss import OSS_ENABLED, get_presigned_url
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task or task.tool_type != "audit":
            return JSONResponse({"success": False, "error": "任务不存在"}, status_code=404)

        # Try TaskFile first (has OSS url)
        tf = db.query(TaskFile).filter(TaskFile.task_id == task_id, TaskFile.file_type == "input").first()
        if tf and tf.oss_url:
            if OSS_ENABLED and tf.oss_url.startswith("oss://"):
                return RedirectResponse(url=get_presigned_url(tf.oss_url))
            elif os.path.exists(tf.oss_url):
                return FileResponse(tf.oss_url, filename=tf.file_name)

        # Fallback: check result_json for uploaded_file
        result = {}
        if task.result_json:
            try: result = json.loads(task.result_json)
            except Exception: pass
        filename = result.get("uploaded_file")
        if not filename:
            return JSONResponse({"success": False, "error": "无上传文件"}, status_code=404)

        file_path = os.path.join(OUTPUTS_DIR, "uploads", str(task_id), filename)
        if not os.path.exists(file_path):
            return JSONResponse({"success": False, "error": "文件不存在"}, status_code=404)
        return FileResponse(file_path, filename=filename)
    finally:
        db.close()
