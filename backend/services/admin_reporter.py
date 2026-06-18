"""
向 admin-dashboard 上报步骤完成
"""
import httpx
import json
from config import ADMIN_API_URL, INTERNAL_API_KEY
from models import StepReport, TaskCreate


async def create_task(
    project_name: str,
    source: str = "frontend",
    input_filename: str = "",
    input_file_count: int = 1,
) -> dict | None:
    """创建任务记录, 返回 task_id"""
    if not ADMIN_API_URL or not INTERNAL_API_KEY:
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{ADMIN_API_URL}/tasks",
                json={
                    "project_name": project_name,
                    "source": source,
                    "input_filename": input_filename,
                    "input_file_count": input_file_count,
                },
                headers={
                    "Authorization": f"Bearer {INTERNAL_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 201 or resp.status_code == 200:
                data = resp.json()
                return data.get("data", data)
            else:
                print(f"[Reporter] create_task failed: {resp.status_code} {resp.text}")
                return None
    except Exception as e:
        print(f"[Reporter] create_task error: {e}")
        return None


async def report_step(
    task_id: str,
    step_type: str,
    status: str,
    usage: dict,
    files: list[dict] = None,
) -> bool:
    """上报步骤完成"""
    if not ADMIN_API_URL or not INTERNAL_API_KEY or not task_id:
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{ADMIN_API_URL}/internal/steps/{step_type}/complete",
                json={
                    "task_id": task_id,
                    "step_type": step_type,
                    "status": status,
                    "usage": usage,
                    "files": files or [],
                },
                headers={
                    "Authorization": f"Bearer {INTERNAL_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 200:
                return True
            else:
                print(f"[Reporter] report_step failed: {resp.status_code} {resp.text}")
                return False
    except Exception as e:
        print(f"[Reporter] report_step error: {e}")
        return False
