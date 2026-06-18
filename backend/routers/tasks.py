"""GET /api/tasks, GET /api/tasks/:id"""
import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from shared_db.models import Task
from auth import get_current_user

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def list_tasks(db: Session = Depends(get_db), user=Depends(get_current_user)):
    tasks = (db.query(Task)
             .filter(Task.user_id == user.id)
             .order_by(Task.created_at.desc())
             .limit(50).all())
    return [{
        "id": t.id, "tool_type": t.tool_type, "status": t.status,
        "input_filename": t.input_filename,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    } for t in tasks]


@router.get("/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    task = db.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    result = None
    if task.result_json:
        try:
            result = json.loads(task.result_json)
        except Exception:
            result = task.result_json
    return {
        "id": task.id, "tool_type": task.tool_type, "status": task.status,
        "input_filename": task.input_filename, "result": result,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


@router.put("/{task_id}/inspector")
def save_inspector(task_id: int, data: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Save inspector results to task"""
    task = db.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    existing = {}
    if task.result_json:
        try: existing = json.loads(task.result_json)
        except Exception: pass
    existing["inspector"] = data
    task.result_json = json.dumps(existing, ensure_ascii=False)
    db.commit()
    return {"success": True}
