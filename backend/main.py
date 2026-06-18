"""audit-platform — FastAPI 入口"""
import os, sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
# shared_db 在项目上级目录
_SHARED = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
if os.path.exists(os.path.join(_SHARED, "shared_db")):
    sys.path.insert(0, _SHARED)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from config import PORT
from shared_db import init_db

app = FastAPI(title="Audit Platform", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

from routers import auth, audit, taxfill, tasks as task_routes
app.include_router(auth.router)
app.include_router(audit.router)
app.include_router(taxfill.router)
app.include_router(task_routes.router)

@app.get("/api/health")
async def health():
    return {"status": "ok"}

@app.on_event("startup")
def on_startup():
    init_db()

# Serve frontend static files (if built)
frontend_dist = os.path.join(BASE_DIR, "..", "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
