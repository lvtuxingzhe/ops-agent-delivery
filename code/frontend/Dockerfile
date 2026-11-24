# 前端开发服务器镜像（第 ① 步：跑 Vite dev server）
#
# 打包成桌面安装包时不用这个镜像，是在本机 npm run build 出 dist/，
# 由 Electron 直接 loadFile。两条路用的是同一份源码和同一份 vite.config.ts。
FROM node:22-alpine

WORKDIR /srv/frontend

# 先拷清单再装依赖，改源码时能复用缓存层
COPY package.json ./
RUN npm install

COPY . .

# --host 0.0.0.0：让容器外的浏览器 / 桌面壳能访问到 dev server
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
