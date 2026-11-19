FROM node:22-bookworm-slim
WORKDIR /app/apps/web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web/index.html apps/web/vite.config.ts apps/web/tsconfig.json ./
COPY apps/web/src ./src
CMD ["node", "node_modules/vite/bin/vite.js", "--host", "0.0.0.0"]
