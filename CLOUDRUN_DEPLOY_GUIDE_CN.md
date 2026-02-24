# OpenFOAM MCP 部署到 Cloud Run（实操指南）

本文按你当前项目形态整理：`openfoam-mcp`，镜像仓库在 Artifact Registry，部署到 `us-central1` 的 Cloud Run。

## 0. 前提

1. 已安装并登录 `gcloud`：
```bash
gcloud auth login
gcloud auth application-default login
```
2. 当前目录是项目根目录（本仓库）：
```bash
cd /home/milsonson/openfoam-mcp
```

## 1. 一次性初始化（只需做一次）

```bash
PROJECT_ID="chinese-astrology-app"
REGION="us-central1"
REPO="openfoam-mcp"
SERVICE="openfoam-mcp"

gcloud config set project "${PROJECT_ID}"

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com

# 若仓库已存在会报 ALREADY_EXISTS，可忽略
gcloud artifacts repositories create "${REPO}" \
  --repository-format=docker \
  --location="${REGION}" \
  --description="OpenFOAM MCP Docker images" || true
```

## 2. 构建并推送镜像（推荐 Cloud Build）

注意：不要把变量拆成带换行的字符串（你之前遇到过 `invalid image name`）。

```bash
IMAGE_TAG="$(date +%Y%m%d-%H%M%S)"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/openfoam-mcp:${IMAGE_TAG}"

echo "IMAGE_URI=${IMAGE_URI}"
gcloud builds submit . --tag "${IMAGE_URI}"
```

## 3. 生成部署环境变量文件

`EOF` 必须单独一行顶格结束。

```bash
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
SERVICE_HOST="${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"

cat > /tmp/openfoam-mcp.env <<EOF
OPENFOAM_MCP_TRANSPORT=streamable-http
OPENFOAM_MCP_HOST=0.0.0.0
OPENFOAM_MCP_PORT=8080
OPENFOAM_MCP_STREAMABLE_HTTP_PATH=/mcp
OPENFOAM_MCP_ARTIFACT_DIR=/app/artifacts
OPENFOAM_MCP_ALLOWED_CASE_ROOTS=/tmp
OPENFOAM_MCP_ARTIFACT_TTL_SECONDS=604800
OPENFOAM_MCP_ARTIFACT_MAX_JOBS=100
OPENFOAM_MCP_ARTIFACT_BASE_URL=https://${SERVICE_HOST}/artifacts
OPENFOAM_MCP_PORTAL_BASE_URL=https://${SERVICE_HOST}/portal
EOF
```

## 4. 部署到 Cloud Run

```bash
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE_URI}" \
  --region "${REGION}" \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 4 \
  --memory 8Gi \
  --concurrency 20 \
  --timeout 3600 \
  --min-instances 1 \
  --max-instances 5 \
  --env-vars-file /tmp/openfoam-mcp.env
```

## 5. 获取服务 URL 并验证

```bash
SERVICE_URL="$(gcloud run services describe "${SERVICE}" --region "${REGION}" --format='value(status.url)')"
echo "${SERVICE_URL}"
```

如需强制用实际 URL 回写 artifact/portal 地址（推荐）：
```bash
gcloud run services update "${SERVICE}" \
  --region "${REGION}" \
  --update-env-vars "OPENFOAM_MCP_ARTIFACT_BASE_URL=${SERVICE_URL}/artifacts,OPENFOAM_MCP_PORTAL_BASE_URL=${SERVICE_URL}/portal"
```

健康检查：
```bash
curl -i "${SERVICE_URL}/health"
```

MCP 接口连通性（streamable-http）：
```bash
curl -i "${SERVICE_URL}/mcp" \
  -H "content-type: application/json" \
  --data '{"jsonrpc":"2.0","id":"init","method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"manual-test","version":"1.0.0"}}}'

curl -i "${SERVICE_URL}/mcp" \
  -H "content-type: application/json" \
  --data '{"jsonrpc":"2.0","id":"tools","method":"tools/list","params":{}}'
```

## 6. 本地 Codex 客户端连接远端 MCP

```bash
codex mcp remove openfoam-remote 2>/dev/null || true
codex mcp add openfoam-remote --url "${SERVICE_URL}/mcp"
codex mcp get openfoam-remote
```

如出现 `timed out awaiting tools/list after 10s`，可把客户端超时调大到 60 秒或更高。

## 7. 部署后常见问题排查

### A. `container failed to start and listen on PORT=8080`
先看 revision 日志：
```bash
gcloud run revisions list --service "${SERVICE}" --region "${REGION}"
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=${SERVICE}" \
  --limit 100 --format='value(textPayload)'
```

重点检查：
1. `OPENFOAM_MCP_TRANSPORT=streamable-http`
2. `OPENFOAM_MCP_HOST=0.0.0.0`
3. `OPENFOAM_MCP_PORT=8080`

### B. `Rate exceeded` / `HTTP 429`
通常是短时间请求过密。处理：
1. 降低本地轮询频率
2. 保留 `min-instances=1`
3. 客户端重试时加退避（例如 1s/2s/4s）

### C. `INVALID_ARGUMENT invalid image name`
通常是变量里混入换行。重新设置：
```bash
IMAGE_TAG="$(date +%Y%m%d-%H%M%S)"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/openfoam-mcp:${IMAGE_TAG}"
echo "${IMAGE_URI}"
```

## 8. 清理旧镜像（只保留最新）

先确认当前服务使用哪个 digest：
```bash
gcloud run services describe "${SERVICE}" --region "${REGION}" \
  --format='value(status.trafficStatuses[0].revisionName)'

gcloud run revisions describe "$(gcloud run services describe "${SERVICE}" --region "${REGION}" --format='value(status.trafficStatuses[0].revisionName)')" \
  --region "${REGION}" \
  --format='value(status.imageDigest)'
```

列出镜像：
```bash
gcloud artifacts docker images list \
  "${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}" \
  --include-tags
```

删除旧 digest（保留当前运行 digest）：
```bash
gcloud artifacts docker images delete \
  "${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/openfoam-mcp@sha256:<OLD_DIGEST>" \
  --delete-tags --quiet
```

## 9. 推荐的日常发布流程

每次只做三步：
1. `gcloud builds submit . --tag "${IMAGE_URI}"`
2. `gcloud run deploy ... --image "${IMAGE_URI}"`
3. `curl ${SERVICE_URL}/health` + `initialize/tools/list` 冒烟测试

这三步通过后，再让客户端切流到新 revision。
