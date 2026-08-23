# ==================== Stage 1: 前端生产构建 ====================
FROM node:20-alpine AS builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ==================== Stage 2: Nginx 静态资源与反向代理 ====================
FROM nginx:alpine
COPY --from=builder /build/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:80/ >/dev/null 2>&1 || exit 1
CMD ["nginx", "-g", "daemon off;"]
