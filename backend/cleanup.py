#!/usr/bin/env python3
"""清理过期的上传文件、引擎日志和输出文件"""
import os, sys, shutil, time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, ".."))
sys.path.insert(0, os.path.join(BASE, "..", ".."))

RETENTION_DAYS = int(os.getenv("FILE_RETENTION_DAYS", "30"))
CUTOFF = time.time() - RETENTION_DAYS * 86400

cleaned = 0

def clean_dir(path):
    global cleaned
    if not os.path.exists(path):
        return
    for name in os.listdir(path):
        full = os.path.join(path, name)
        try:
            st = os.stat(full)
            if st.st_mtime < CUTOFF:
                if os.path.isdir(full):
                    shutil.rmtree(full)
                else:
                    os.remove(full)
                cleaned += 1
        except Exception as e:
            print(f"  Skip {full}: {e}")

# 上传文件
clean_dir(os.path.join(BASE, "..", "outputs", "uploads"))

# 引擎日志
engines_dir = os.path.join(BASE, "engines")
for engine in ["audit", "taxfill"]:
    clean_dir(os.path.join(engines_dir, engine, "logs"))

print(f"Cleaned {cleaned} files/folders older than {RETENTION_DAYS} days")
