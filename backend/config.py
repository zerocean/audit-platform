"""audit-platform 配置"""
import os, sys
from dotenv import load_dotenv

load_dotenv()

# 确保能找到 shared_db (D:\Demo\shared_db\)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# -- DashScope --
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# -- Models --
VISION_MODEL = os.getenv("VISION_MODEL", "qwen3.6-flash")
AUDIT_MODEL = os.getenv("AUDIT_MODEL", "deepseek-v4-flash")
AUDIT_MAX_TOKENS = 16384
INSPECTOR_MODEL = os.getenv("INSPECTOR_MODEL", "qwen3.6-plus")

# -- JWT --
JWT_SECRET="audit-platform-dev-secret-change-me"
JWT_ALGORITHM = "HS256"
JWT_EXPIRES_IN = 86400

# -- Port --
PORT = int(os.getenv("PORT", "8767"))

# -- Tracking: 仅线上统计 (ENV=production 时启用)
TRACKING_ENABLED = os.getenv("ENV", "").lower() == "production"

# -- Internal API Key (供 email_worker 等内部服务调用)
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "audit-platform-internal-key-change-me")

# -- Paths --
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUTS_DIR = os.path.join(os.path.dirname(BASE_DIR), "outputs")
ENGINES_DIR = os.path.join(BASE_DIR, "engines")
AUDIT_REVIEW_DIR = os.path.join(ENGINES_DIR, "audit")
TAXFILL_HK_DIR = os.path.join(ENGINES_DIR, "taxfill")
