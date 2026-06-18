"""
阿里云 OSS 文件存储服务
"""
import os
import oss2
from datetime import datetime, timedelta
from config import OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, OSS_BUCKET, OSS_ENDPOINT


def _get_bucket():
    """获取 OSS bucket 实例"""
    if not OSS_ACCESS_KEY_ID or not OSS_ACCESS_KEY_SECRET:
        raise RuntimeError("OSS 凭证未配置 (OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET)")

    auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    return oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)


def _oss_key(project: str, folder: str, run_id: str, filename: str) -> str:
    """生成 OSS key, 按 DESIGN.md 规范组织"""
    now = datetime.now()
    return f"{project}/{folder}/{now.year}/{now.month:02d}/{now.day:02d}/{run_id}_{filename}"


def upload_file(local_path: str, project: str, folder: str, run_id: str, filename: str = None) -> str:
    """
    上传文件到 OSS
    返回 oss_url (oss://bucket/key)
    """
    if filename is None:
        filename = os.path.basename(local_path)

    key = _oss_key(project, folder, run_id, filename)

    try:
        bucket = _get_bucket()
        bucket.put_object_from_file(key, local_path)
        return f"oss://{OSS_BUCKET}/{key}"
    except Exception as e:
        print(f"[OSS] Upload failed: {e}")
        raise


def upload_bytes(data: bytes, project: str, folder: str, run_id: str, filename: str, content_type: str = None) -> str:
    """上传 bytes 到 OSS"""
    key = _oss_key(project, folder, run_id, filename)

    try:
        bucket = _get_bucket()
        headers = {}
        if content_type:
            headers['Content-Type'] = content_type
        bucket.put_object(key, data, headers=headers)
        return f"oss://{OSS_BUCKET}/{key}"
    except Exception as e:
        print(f"[OSS] Upload failed: {e}")
        raise


def get_presigned_url(oss_url: str, expires: int = 3600) -> str:
    """生成预签名下载 URL (1小时有效)"""
    # oss_url 格式: oss://bucket/key
    key = oss_url.replace(f"oss://{OSS_BUCKET}/", "")

    try:
        bucket = _get_bucket()
        return bucket.sign_url('GET', key, expires)
    except Exception as e:
        print(f"[OSS] Presign failed: {e}")
        raise


def is_configured() -> bool:
    """检查 OSS 是否已配置"""
    return bool(OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET)


def file_exists(oss_url: str) -> bool:
    """检查 OSS 文件是否存在"""
    key = oss_url.replace(f"oss://{OSS_BUCKET}/", "")
    try:
        bucket = _get_bucket()
        return bucket.object_exists(key)
    except Exception:
        return False
