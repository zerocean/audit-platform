"""
阿里云 OSS 文件存储服务
Key 规则: ai_workspace/{tool}/{task_id}/{filename}
"""
import os
import oss2
from config import OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, OSS_ENDPOINT, OSS_BUCKET, OSS_PREFIX, OSS_ENABLED


def _get_bucket():
    """获取 OSS bucket 实例"""
    if not OSS_ACCESS_KEY_ID or not OSS_ACCESS_KEY_SECRET:
        raise RuntimeError("OSS 凭证未配置")
    auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    return oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)


def _oss_key(tool: str, task_id: int, filename: str) -> str:
    """生成 OSS key: ai_workspace/audit/123/report.pdf"""
    return f"{OSS_PREFIX}/{tool}/{task_id}/{filename}"


def upload_file(local_path: str, tool: str, task_id: int, filename: str = None) -> str:
    """上传本地文件到 OSS，返回 oss_url"""
    if filename is None:
        filename = os.path.basename(local_path)
    key = _oss_key(tool, task_id, filename)
    bucket = _get_bucket()
    bucket.put_object_from_file(key, local_path)
    return f"oss://{OSS_BUCKET}/{key}"


def upload_bytes(data: bytes, tool: str, task_id: int, filename: str, content_type: str = None) -> str:
    """上传 bytes 到 OSS，返回 oss_url"""
    key = _oss_key(tool, task_id, filename)
    bucket = _get_bucket()
    headers = {}
    if content_type:
        headers['Content-Type'] = content_type
    bucket.put_object(key, data, headers=headers)
    return f"oss://{OSS_BUCKET}/{key}"


def get_presigned_url(oss_url: str, expires: int = 3600) -> str:
    """生成预签名下载 URL（默认1小时有效）"""
    key = oss_url.replace(f"oss://{OSS_BUCKET}/", "")
    bucket = _get_bucket()
    return bucket.sign_url('GET', key, expires)


def download_to_bytes(oss_url: str) -> bytes:
    """从 OSS 下载文件内容"""
    key = oss_url.replace(f"oss://{OSS_BUCKET}/", "")
    bucket = _get_bucket()
    result = bucket.get_object(key)
    return result.read()


def download_to_file(oss_url: str, local_path: str):
    """从 OSS 下载到本地文件"""
    key = oss_url.replace(f"oss://{OSS_BUCKET}/", "")
    bucket = _get_bucket()
    bucket.get_object_to_file(key, local_path)


def file_exists(oss_url: str) -> bool:
    """检查 OSS 文件是否存在"""
    key = oss_url.replace(f"oss://{OSS_BUCKET}/", "")
    try:
        return _get_bucket().object_exists(key)
    except Exception:
        return False


def delete_file(oss_url: str):
    """删除 OSS 文件"""
    key = oss_url.replace(f"oss://{OSS_BUCKET}/", "")
    _get_bucket().delete_object(key)


def upload_audit_file(local_path: str, task_id: int, filename: str) -> str:
    """上传审计工具文件"""
    return upload_file(local_path, "audit", task_id, filename)


def upload_taxfill_file(local_path: str, task_id: int, filename: str) -> str:
    """上传 TaxFill 工具文件"""
    return upload_file(local_path, "taxfill", task_id, filename)


def upload_audit_bytes(data: bytes, task_id: int, filename: str) -> str:
    """上传审计工具文件（bytes）"""
    return upload_bytes(data, "audit", task_id, filename)


def upload_taxfill_bytes(data: bytes, task_id: int, filename: str) -> str:
    """上传 TaxFill 工具文件（bytes）"""
    return upload_bytes(data, "taxfill", task_id, filename)


# ── 本地文件回退 ──

def upload_or_local(local_path: str, tool: str, task_id: int, filename: str) -> str:
    """OSS 可用时上传到 OSS，否则返回本地路径"""
    if OSS_ENABLED:
        return upload_file(local_path, tool, task_id, filename)
    return local_path


def download_or_local(oss_url: str, local_fallback: callable):
    """
    OSS 可用时从 OSS 下载，否则调用 local_fallback()
    local_fallback 应返回 FastAPI Response 或 FileResponse
    """
    if OSS_ENABLED and oss_url and oss_url.startswith("oss://"):
        try:
            key = oss_url.replace(f"oss://{OSS_BUCKET}/", "")
            url = _get_bucket().sign_url('GET', key, 3600)
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=url)
        except Exception as e:
            print(f"[OSS] Download failed: {e}, falling back to local")
    return local_fallback()
