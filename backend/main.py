"""audit-platform — FastAPI 入口"""
import os, sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
_SHARED = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
if os.path.exists(os.path.join(_SHARED, "shared_db")):
    sys.path.insert(0, _SHARED)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from config import PORT
from shared_db import init_db

app = FastAPI(title="Audit Platform", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# API routes must be registered BEFORE static files
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

# Frontend static files + SPA fallback
FRONTEND_DIST = os.path.join(BASE_DIR, "..", "frontend", "dist")
if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")
    
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """非 API 路径回退到 index.html（支持 React Router）"""
        file_path = os.path.join(FRONTEND_DIST, full_path) if full_path else None
        if file_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        index = os.path.join(FRONTEND_DIST, "index.html")
        if os.path.isfile(index):
            return FileResponse(index)
        return {"detail": "Not Found"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
