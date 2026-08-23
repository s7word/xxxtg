# ==================== Stage 1: 前端生产构建 ====================
# 在容器内独立构建前端静态产物，使镜像构建完全自包含，不依赖宿主机预先执行过 npm run build
FROM node:20-alpine AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ==================== Stage 2: Python 后端运行时 ====================
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/requirements.txt
# 主用清华 PyPI 镜像加速安装，同时保留官方源作为兜底候选，避免镜像不可达时构建卡死/失败
RUN pip install --no-cache-dir \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    --extra-index-url https://pypi.org/simple \
    --timeout 120 \
    -r requirements.txt

COPY backend /app/backend
COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data
ENV FRONTEND_DIST=/app/frontend/dist

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
