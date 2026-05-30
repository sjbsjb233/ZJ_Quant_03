# web-frontend — build the SPA, serve it from nginx + reverse-proxy /api to
# web-backend (容器化重构 V3 §5.1/§8.3). Build context = repo root (compose).

# --- stage 1: build the SPA -------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /fe
COPY apps/web/frontend/package.json apps/web/frontend/package-lock.json* ./
RUN npm ci
COPY apps/web/frontend/ ./
RUN npm run build

# --- stage 2: nginx static + reverse proxy ----------------------------------
FROM nginx:alpine AS serve
COPY apps/web/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /fe/dist /usr/share/nginx/html

EXPOSE 80
# nginx:alpine's default CMD already runs nginx -g 'daemon off;'
