#!/usr/bin/env python3
"""
将服务器上现有的本地文件迁移到 OSS

用法:
  python migrate_to_oss.py [--dry-run]

dry-run 模式只打印将要上传的文件列表，不实际上传。
"""
import os
import sys

# 加载配置
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
from config import OUTPUTS_DIR, OSS_ENABLED
from services.oss import upload_file

if not OSS_ENABLED:
    print("OSS 未配置，请先在 .env 里设置 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET")
    sys.exit(1)

DRY_RUN = "--dry-run" in sys.argv

# 1. 上传 uploads 目录下的输入文件
uploads_dir = os.path.join(OUTPUTS_DIR, "uploads")
total = 0
success = 0

if os.path.isdir(uploads_dir):
    for task_dir in sorted(os.listdir(uploads_dir)):
        task_path = os.path.join(uploads_dir, task_dir)
        if not os.path.isdir(task_path):
            continue
        try:
            task_id = int(task_dir)
        except ValueError:
            continue

        for filename in os.listdir(task_path):
            local_path = os.path.join(task_path, filename)
            if not os.path.isfile(local_path):
                continue

            # 判断工具类型（从文件名推测或从目录推断）
            if task_dir.startswith("audit_") or "_review" in filename:
                tool = "audit"
            else:
                tool = "taxfill"

            total += 1
            oss_key = f"ai_workspace/{tool}/{task_id}/{filename}"

            if DRY_RUN:
                print(f"[DRY-RUN] {local_path} → oss://.../{oss_key}")
            else:
                try:
                    url = upload_file(local_path, tool, task_id, filename)
                    print(f"[OK] {local_path} → {url}")
                    success += 1
                except Exception as e:
                    print(f"[FAIL] {local_path}: {e}")

print(f"\n{'[DRY-RUN] ' if DRY_RUN else ''}共 {total} 个文件" + (f"，上传成功 {success}" if not DRY_RUN else ""))

# 2. 更新数据库中的 oss_url（需要连接 PostgreSQL）
if not DRY_RUN and success > 0:
    print("\n⚠️  数据库中的 oss_url 字段仍指向本地路径，需执行 SQL 更新：")
    print("UPDATE task_files SET oss_url = REPLACE(oss_url, '/opt/audit-platform/outputs/', 'oss://audit-ha-bucket/ai_workspace/');")
    print("（请根据实际情况调整路径前缀）")
