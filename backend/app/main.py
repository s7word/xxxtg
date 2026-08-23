import os
import logging
from pathlib import Path
from typing import Optional
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
    version="2.2.0",
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
        "version": "2.2.0",
        "engine": "Distributed State Machine & Crypto Context Auditor"
    }

# 2. 定位前端静态文件目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", str(BASE_DIR / "frontend" / "dist"))).resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    """兼容 Python 3.8：优先 Path.is_relative_to，否则比较 resolve 后的 parts。"""
    try:
        if hasattr(path, "is_relative_to"):
            return path.is_relative_to(root)
    except (ValueError, OSError, TypeError):
        return False
    return path.parts[: len(root.parts)] == root.parts


def resolve_spa_file(full_path: str, root: Optional[Path] = None) -> Optional[Path]:
    """将 SPA 请求路径解析为 root 内的安全文件；穿越或越界一律返回 None。"""
    base = (root or FRONTEND_DIST).resolve()
    raw = (full_path or "").strip()
    if not raw or raw in {".", "/"}:
        return None
    # 拒绝绝对路径、UNC、以及显式 .. 段，避免依赖 resolve 的边界行为
    candidate = Path(raw)
    if candidate.is_absolute() or raw.startswith(("\\", "/")):
        return None
    if ".." in candidate.parts:
        return None
    try:
        file_path = (base / raw).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not _is_relative_to(file_path, base):
        return None
    if file_path.exists() and file_path.is_file():
        return file_path
    return None


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
        safe_file = resolve_spa_file(full_path, FRONTEND_DIST)
        if safe_file is not None:
            return FileResponse(str(safe_file))
        return FileResponse(str(FRONTEND_DIST / "index.html"))
else:
    logger.warning(f"未找到前端构建产物目录: {FRONTEND_DIST}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
