# 用户应用 Dockerfile 模板

## 标准模板（支持 Verdaccio 加速）

用户应用的 Dockerfile 应该使用以下模板，以支持：
1. Verdaccio npm 缓存加速
2. 没有 package-lock.json 的情况
3. 灵活的构建配置

```dockerfile
FROM node:18-alpine

WORKDIR /app

# 接收构建参数（Runner 会自动传递）
ARG NPM_REGISTRY=https://registry.npmjs.org

# 复制 .npmrc（如果存在，优先使用）
COPY .npmrc* ./

# 如果没有 .npmrc，使用构建参数配置 registry
RUN if [ ! -f .npmrc ]; then \
      echo "registry=${NPM_REGISTRY}" > .npmrc; \
    fi

# 复制依赖文件
COPY package*.json ./

# 安装依赖（优先使用 npm ci，如果没有 lockfile 则使用 npm install）
RUN npm ci 2>/dev/null || npm install

# 复制源代码
COPY . .

# 构建（如果有 build 脚本）
RUN npm run build 2>/dev/null || echo "No build script, skipping..."

# 暴露端口
EXPOSE 3000

# 启动命令
CMD ["npm", "start"]
```

## 关键点说明

### 1. NPM_REGISTRY 构建参数

```dockerfile
ARG NPM_REGISTRY=https://registry.npmjs.org
```

- Runner 会自动传递 `--build-arg NPM_REGISTRY=http://172.21.138.103:4873`
- 默认值是 npmjs.org，作为兜底

### 2. .npmrc 优先级

```dockerfile
COPY .npmrc* ./
RUN if [ ! -f .npmrc ]; then \
      echo "registry=${NPM_REGISTRY}" > .npmrc; \
    fi
```

- 如果用户 commit 了 .npmrc，优先使用用户的配置
- 否则使用 Runner 传递的 NPM_REGISTRY

### 3. 兼容没有 package-lock.json

```dockerfile
RUN npm ci 2>/dev/null || npm install
```

- 优先使用 `npm ci`（快速且可重现）
- 如果没有 lockfile，fallback 到 `npm install`

### 4. 可选的 build 步骤

```dockerfile
RUN npm run build 2>/dev/null || echo "No build script, skipping..."
```

- 如果 package.json 有 build 脚本，执行它
- 否则跳过（不报错）

## Workspace 自动生成 .npmrc

为了更好的用户体验，Workspace 在执行 `npm install` 时会自动生成 `.npmrc`：

```bash
# workspace 容器中
echo "registry=http://172.21.138.103:4873" > .npmrc
npm install
```

用户只需要 commit 这个文件，部署时就能自动使用 Verdaccio 加速。

## 完整流程

### 开发态（Workspace）

1. 用户执行 `npm install`
2. Workspace 自动创建 `.npmrc` → `registry=http://172.21.138.103:4873`
3. npm 从 Verdaccio 安装依赖
4. 生成 `package-lock.json`
5. 用户 commit 并 push `.npmrc` 和 `package-lock.json`

### 部署态（Runner）

1. Runner 从 Git 拉取代码
2. Runner 执行 `docker build --build-arg NPM_REGISTRY=http://172.21.138.103:4873`
3. Dockerfile 检测到 `.npmrc` 存在，使用它
4. 或者使用 build-arg 传递的 NPM_REGISTRY
5. npm 从 Verdaccio 安装依赖（快速）
6. 构建镜像成功

## 最佳实践

### 推荐做法

1. ✅ 在 Workspace 中执行 `npm install`，自动生成 `.npmrc`
2. ✅ Commit `.npmrc` 和 `package-lock.json`
3. ✅ 使用上面的标准 Dockerfile 模板

### 也可以工作（但不推荐）

1. ⚠️ 不 commit `.npmrc`，依赖 Runner 的 build-arg
2. ⚠️ 不 commit `package-lock.json`，每次部署安装最新版本

### 不推荐

1. ❌ 在 Dockerfile 中硬编码 registry 地址
2. ❌ 使用 `npm install --registry=...` 命令行参数

## 故障排查

### 问题：部署时 npm install 很慢

**原因**：没有使用 Verdaccio

**解决**：
1. 检查 `.npmrc` 是否存在且内容正确
2. 检查 Runner 的 `NPM_REGISTRY` 配置
3. 检查 Verdaccio 服务是否正常

### 问题：部署失败，提示找不到 package-lock.json

**原因**：Dockerfile 使用了 `npm ci` 但没有 lockfile

**解决**：
1. 使用上面的标准模板（支持 fallback）
2. 或者在 Workspace 中执行 `npm install` 生成 lockfile

### 问题：依赖版本不一致

**原因**：没有 package-lock.json，每次安装最新版本

**解决**：
1. 在 Workspace 中执行 `npm install`
2. Commit `package-lock.json`
3. 部署时会使用锁定的版本
