# 容器、Kubernetes 與 CI/CD

這個服務是一個管理多個 Discord Bot 的 Web 控制台。容器啟動 `python -m web`，控制台再為每份 Bot 設定啟動獨立 subprocess。Ollama、OpenAI 相容 API、Gemini、Grok 及 ComfyUI 都是外部服務；這個映像不包含模型權重、不需要 GPU，也不會在啟動時自動下載模型。

## Docker Compose：給自己或小型團隊使用

需求：Docker Engine/Desktop 可用，且支援 Docker Compose。WSL 使用 Docker Desktop 時，先在 Docker Desktop → Settings → Resources → WSL Integration 啟用該發行版。

在專案根目錄執行：

```bash
cp .env.deploy.example .env.deploy
chmod 600 .env.deploy
```

Windows PowerShell 可改用 `Copy-Item .env.deploy.example .env.deploy`，再以 `notepad .env.deploy` 編輯；後續 `docker compose` 指令相同，不需執行 Linux 的 `chmod`。

編輯 `.env.deploy`，填入至少 12 字元、每個部署各自不同的管理密碼：

```dotenv
CONTROL_PANEL_PASSWORD=請換成自己的長密碼
CONTROL_PANEL_PORT=8080
```

啟動與查看狀態：

```bash
docker compose --env-file .env.deploy up --build -d
docker compose --env-file .env.deploy ps
docker compose --env-file .env.deploy logs --tail=100 -f
```

瀏覽器開啟 `http://localhost:8080`，登入後建立 Bot 設定、填入 Discord Bot Token、選擇模型 Provider 與 API 位址，測試連線後啟動 Bot。每位部署者使用自己的 Discord Application、Token、模型 API 與金鑰，不需要修改原始碼。Discord Developer Portal 仍需開啟 Message Content Intent，並依 README 的邀請權限設定邀請 Bot。

Compose 只將控制台綁到宿主機 `127.0.0.1`。從其他電腦管理時，透過 SSH tunnel 使用：

```bash
ssh -L 8080:127.0.0.1:8080 your-user@your-server
```

容器中的 `127.0.0.1` 是容器本身。Ollama 在同一台宿主機時，GUI 填 `http://host.docker.internal:11434`；Ollama 在另一台 Windows 電腦時，填該主機可連通的區網位址，例如 `http://192.168.1.100:11434`（請替換為自己的主機 IP）。Ollama 必須監聽可達的介面，宿主機防火牆也必須允許容器所在網路連入。OpenAI 相容 API 的 Base URL 通常包含 `/v1`。ComfyUI 同樣使用宿主機或遠端可連通的 `8188` 位址。

## 持久化、更新與備份

Compose 使用 named volume `control-panel-data`，掛載到 `/data/control-panel`。控制台設定、每份 Bot 的設定與 log，以及 Bot 的 SQLite、角色圖等 runtime 資料都存放在這個 volume，而非映像裡。設定檔含 Discord/API Token；volume 備份須視為機密資料管理。

更新與停止：

```bash
docker compose --env-file .env.deploy up --build -d
docker compose --env-file .env.deploy stop
```

`docker compose down` 會移除容器但保留 named volume；`down -v` 會刪除設定、歷史及圖片，不應作為一般更新指令。

一致性備份請先停止服務，再備份整個 volume；如此 SQLite 與相關設定、圖片會一併保存。取得實際 volume 名稱：

```bash
docker compose --env-file .env.deploy volumes -q
```

若 Docker 版本沒有 `compose volumes`，以 `docker volume ls` 查找專案名稱開頭、`control-panel-data` 結尾的 volume。將它掛載到備份工具容器並唯讀封存到自己的備份目錄。還原時必須恢復整個目錄，並維持 UID/GID `10001:10001`。

映像使用非 root UID `10001`、唯讀根檔案系統、可寫 `/tmp`，包含 TTS 使用的 `ffmpeg` 與 `libopus0`。若改用 bind mount，先建立專用資料目錄並賦予 UID `10001` 寫入權限。不要將原專案根目錄直接掛載到 `/app`。

`.dockerignore` 只允許 runtime 程式碼及依賴清單進入 build context，Dockerfile 也只複製這些來源；既有 `.env`、`.venv`、SQLite、圖片、log、SSH key 都不會打包進映像。容器與 Kubernetes 預設禁止舊 `.env` 匯入；只有明確啟用 `CONTROL_PANEL_ALLOW_LEGACY_IMPORT=true` 的本機遷移程序才允許匯入。

## Kubernetes 首次部署

需求：已可操作的 Kubernetes 叢集、相容版本的 `kubectl`、可動態配置儲存空間的預設 StorageClass，以及叢集可拉取的映像。程式提供 Kustomize manifests；不會自行安裝叢集或建立公網 Ingress。

`deploy/k8s/base` 包含 Deployment、ClusterIP Service、10Gi PVC、無 Token 掛載的 ServiceAccount，以及不含密碼的 ConfigMap。Deployment 固定一份副本、`Recreate` 更新策略，讓舊 Pod 完全終止後再啟動新的 Bot gateway session；目前不支援對同一份設定水平擴容。

先建立 namespace 與控制台 Secret。以下 Bash 使用互動讀取，不把實際密碼寫進 shell history 或 YAML：

```bash
kubectl create namespace discord-ai
read -rsp 'Control panel password (at least 12 characters): ' control_panel_password
printf '\n'
printf '%s' "$control_panel_password" | kubectl --namespace discord-ai create secret generic discord-ai-auth --from-file=password=/dev/stdin
unset control_panel_password
```

執行已建置映像的部署。例如 CI 發布到 `ghcr.io/your-account/your-repository`：

```bash
export DEPLOY_IMAGE=ghcr.io/your-account/your-repository:sha-完整40字元commit
bash scripts/deploy-k8s.sh
```

`render_k8s.py` 建立暫存 Kustomize overlay，將該映像寫入 manifest，然後 `deploy-k8s.sh` 套用並等待 rollout readiness。也可只預覽，不寫入叢集：

```bash
python3 scripts/render_k8s.py --image ghcr.io/your-account/your-repository:sha-完整40字元commit
```

腳本也接受 `ghcr.io/your-account/your-repository@sha256:<64位digest>`，拒絕未指定版本及 `latest`。使用其他 namespace 時，先在該 namespace 建立 Secret，再設定 `DEPLOY_NAMESPACE`；render 腳本會一併替換 namespace。

若 GHCR package 不是公開的，先在 namespace 建立 image pull Secret，並以自己的 Kustomize overlay 為 Pod 加入 `imagePullSecrets`；不要把 PAT 寫進 git。沒有預設 StorageClass 時，在 overlay 指定 PVC 的 `storageClassName`。尚未綁定的 PVC 會使 Pod 保持 Pending，必須先解決儲存供應問題。

取得 UI：

```bash
kubectl --namespace discord-ai port-forward service/discord-ai 8080:8080
```

然後開啟 `http://localhost:8080`。`/healthz` 為 process liveness，`/readyz` 為控制台資料儲存／管理狀態 readiness；不要求外部模型或所有 Bot 已登入，模型離線應在 GUI 的連線測試與 Bot 狀態中顯示，不應導致 Pod 不斷重新啟動。

Pod 給控制台與 Bot 共 30 秒關機時間；資源預設 request `100m CPU / 256Mi`、limit `2 CPU / 2Gi`。多 Bot、長對話或語音負載增加時應調整 limits/PVC，仍維持一份副本。外部模型與 Discord HTTPS/WebSocket 必須能從 Pod 網路連到；Kubernetes 中沒有通用的 `host.docker.internal`。

若日後需要對外服務，以自己的 HTTPS reverse proxy/Ingress、憑證與存取政策包裝 ClusterIP Service。此版本是一個部署管理者的控制台；多組使用者各自部署自己的 instance/PVC/Secret，並非提供任意訪客註冊的共享 SaaS。

## GitHub Actions CI：測試、建置、發布

`.github/workflows/ci.yaml` 在 Pull Request、`main` push、`v*` tag 或手動觸發時：

1. 安裝 Python 3.12 依賴，跑 `tests/`、Python compile、瀏覽器 JavaScript 語法與 Kustomize render，並用 kubeconform 檢查 Kubernetes 1.37 resource schemas。
2. 建置實際 Docker image，使用非 root、唯讀根檔案系統、named volume 啟動，驗證 health/readiness、登入、設定 CRUD、秘密遮罩，以及容器重啟後設定仍保留。
3. 容器發布預設關閉。只有 repository Actions variable `PUBLISH_CONTAINER` 明確設為字串 `true`（`vars.PUBLISH_CONTAINER == 'true'`），且事件為 `main` 或 `v*` 的 push 時，才將通過測試的同一份映像發布到 `ghcr.io/<owner>/<repository>:sha-<完整commit>`。PR 不登入或推送 registry，也不需要真實 Discord Token 或模型金鑰。

啟用發布前，請至 repository → Settings → Secrets and variables → Actions → Variables 設定 `PUBLISH_CONTAINER=true`，確認 GHCR package 權限與預期可見範圍。沒有設定這個變數時，push 仍會測試與建置，但不會推送映像。Windows EXE 建置另由 `ENABLE_WINDOWS_BUILD=true` 或手動 `workflow_dispatch` 明確啟用，詳見 [桌面版發行說明](windows-desktop.md#ci-與發行)；一般 push 不會預設產生公開下載的 binary。

repository 名稱自動由 `github.repository` 轉小寫，不需要改寫 registry 路徑。Actions job 只有讀取 source 的權限；發布映像的 job 另外取得 `packages:write`，使用 GitHub 自動提供的 `GITHUB_TOKEN`，不需要把個人 PAT 存進 repository。所有第三方 Actions 都固定 commit SHA；版本更新時先確認上游 release 與 SHA，再修改。

首次發布若出現權限錯誤，檢查 repository 的 Actions permissions、組織 package policy，及既有 GHCR package 是否允許該 repository 的 Actions 存取。預設發布 `linux/amd64`，若部署 ARM64 叢集，需先擴充為 Buildx multi-platform build。

## 手動 CD：核准後部署指定版本

`.github/workflows/deploy.yaml` 只接受在 `main` 執行的 `workflow_dispatch`；不會在 push 後直接更新叢集。

部署管理員先完成以下一次性設定：

1. 在 GitHub repository → Settings → Environments 建立 `production`，設定 Required reviewers 與只允許 `main` 的部署分支限制。Environment 名稱本身不會自動啟用人工核准，必須在 GitHub 端設定。
2. 手動建立 Kubernetes namespace、`discord-ai-auth` Secret，以及需要的 imagePullSecret、StorageClass。CD 不讀取或建立模型/API/Discord 金鑰。
3. 建立僅能在 `discord-ai` namespace 部署的 kubeconfig。`deploy/k8s/ci-rbac.yaml` 提供最小部署 Role/RoleBinding 參考，請由管理員先套用；應依叢集既有短期／身分認證方式簽發 kubeconfig，不使用 admin kubeconfig。
4. 將 kubeconfig 的 base64 放進 GitHub `production` environment secret `KUBECONFIG_BASE64`，不要提交到 git。若用短期憑證，需在過期前更新。
5. GitHub-hosted runner 必須能連到 Kubernetes API。區網 private cluster 應改用可達叢集的 self-hosted runner，或自行建立到 runner 的私有網路連線。

在 Actions → Deploy approved image to Kubernetes → Run workflow，指定 CI 已發布的 `sha-<完整40字元commit>`，並填寫與叢集相差不超過一個 minor 的 `kubectl` 版本。預設 `v1.37.1` 僅是本專案建立時驗證過的版本，不代表適合每個叢集。workflow 驗證輸入、從官方站點下載並核對 kubectl SHA256、在暫存目錄載入 kubeconfig、套用固定映像並等待 rollout，完成後移除 runner 上的 kubeconfig。

回退到上一個已發布且驗證過的 sha tag，重跑同一個 CD workflow。這會重新啟動 Bot；PVC、Token、角色與記憶不受影像版本替換影響。`Recreate` 更新期間控制台與 Bot 會短暫離線。

## 排查

```bash
kubectl --namespace discord-ai get pods,pvc,services
kubectl --namespace discord-ai describe deployment discord-ai
kubectl --namespace discord-ai logs deployment/discord-ai --tail=100
```

`CreateContainerConfigError` 常見原因是尚未建立 `discord-ai-auth`；`ImagePullBackOff` 檢查 image tag 與 registry 存取；`Pending` 檢查 PVC/StorageClass；資料目錄 permission denied 檢查儲存驅動是否支援 fsGroup `10001`。控制台可用但 Bot 無法上線時，在 GUI 查看該 Bot log，先確認 Discord Token、Intent、API endpoint，以及外部服務的防火牆與路由。

## 官方參考

- [Docker build best practices](https://docs.docker.com/build/building/best-practices/)
- [Kubernetes Kustomize](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/)
- [Kubernetes Secrets](https://kubernetes.io/docs/concepts/configuration/secret/)
- [kubectl version skew policy](https://kubernetes.io/releases/version-skew-policy/#kubectl)
- [GitHub deployment environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
