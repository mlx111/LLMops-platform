# LLMOps 平台前端镜像（多阶段构建：node 编译 → nginx 托管）
# 国内构建可覆盖 npm 源：docker build --build-arg NPMMIRROR=https://registry.npmmirror.com

# ---- 构建阶段 ----
FROM docker.1ms.run/library/node:24-alpine AS build

ARG NPMMIRROR=https://registry.npmmirror.com
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm config set registry ${NPMMIRROR} && npm ci

COPY . .
# 生产构建产物在 /app/dist（.env.production 中 VITE_API_BASE_URL=/api）
RUN npm run build

# ---- 运行阶段 ----
FROM docker.1ms.run/library/nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
