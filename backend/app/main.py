import os
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.app.api.routes import router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger("EdgeNodeApp")

app = FastAPI(
    title="EdgeNode-Auditor Simulation Engine",
    version="2.1.0",
    description="分布式多协议边缘节点状态机仿真、带外挑战响应与密码学上下文审计框架"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. 优先挂载 API 路由
app.include_router(api_router)

@app.get("/api/health", summary="系统健康检查探针")
async def health_check():
    return {
        "status": "ok",
        "system": "EdgeNode-Auditor",
        "version": "2.1.0",
        "engine": "Distributed State Machine & Crypto Context Auditor"
    }

# 2. 定位前端静态文件目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", str(BASE_DIR / "frontend" / "dist"))).resolve()

if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():
    logger.info(f"前端静态文件目录已挂载: {FRONTEND_DIST}")
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    async def serve_root():
        return FileResponse(str(FRONTEND_DIST / "index.html"))

    @app.get("/{full_path:path}")
    async def serve_spa_frontend(full_path: str):
        file_path = FRONTEND_DIST / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(FRONTEND_DIST / "index.html"))
else:
    logger.warning(f"未找到前端构建产物目录: {FRONTEND_DIST}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
