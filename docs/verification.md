# 交付驗證紀錄

本次交付以 Windows EXE、可自架的通用 GUI、Discord Bot 執行管理、Ollama／閉源 API、Docker/Kubernetes 封裝與 CI/CD 原始碼為範圍。沒有將使用者的 Bot Token 發佈到 GitHub，也沒有對未指定的正式叢集進行部署。

| 需求 | 實作位置 | 驗證方式 |
|---|---|---|
| 通用 GUI 與登入 | `web/static/`、`web/app.py` | 真實 Chromium/Puppeteer 測試登入、建立／編輯／刪除、登出；桌面及390px手機檢查，無 JS/CSP 錯誤 |
| Windows EXE | `desktop/`、`desktop_entry.py`、`packaging/` | Windows Python 3.12 實際 PyInstaller one-file 建置；打包後 HTTP 與 Bot 入口 JSON 自測；實際開啟原生 WebView2 視窗、自動登入、關閉後正常退出 |
| 桌面快速設定 | `web/static/`、`web/discord_setup.py` | 7 款服務預設、裸 IP 補址、驗證 Token 產生邀請連結、一鍵儲存啟動；Chromium 模擬 API 驗證錯誤與空模型，不呼叫真實 Discord |
| 生圖選用與安裝檢查 | `web/image_setup.py`、`web/runtime.py`、GUI | 預設關閉、逐項結果、啟動前強制重驗；fake HTTP 覆蓋離線／缺檔／缺節點／schema／redirect／逾時。測試區網主機的 14/14 項安裝檢查通過，僅送出 11 個 GET，未執行生成 |
| 自行設定 Discord Bot | `web/models.py`、`web/store.py`、`web/runtime.py` | API 測試 Token 遮罩／保留／清除、權限、Intent、資料隔離、啟停／重啟；使用假 Bot 程序，沒有登入真實 Discord |
| Ollama 與閉源 API | `providers/` | 本機假的 HTTP server 驗證 Ollama、OpenAI相容、Gemini／Anthropic原生協定；測試 headers、payload、回應、錯誤及分頁；GUI後端對測試區網 Ollama 主機 probe成功，取得4個模型 |
| 推理模型參數 | `providers/openai_compatible.py`、GUI進階欄位 | `max_tokens`／`max_completion_tokens` 互斥、temperature可省略；官方參數核對與 fake HTTP 測試；瀏覽器保存／重載確認 |
| 既有 Bot 功能 | `bot.py`、記憶／生圖／TTS模組 | 保留原 Discord 指令與資料流程；GUI 子程序強制使用各自的 state/SQLite/characters 目錄，並修正非 Ollama 預設 Provider 的模型初始化 |
| 程序生命週期 | `web/runtime.py` | 測試同Token重複啟動阻擋、重啟套用設定、日誌遮罩、正常關機、恢復標記、start/delete與stop/start競態 |
| Docker 封裝 | `Dockerfile`、`compose.yaml` | 實際建置映像，非root UID10001、唯讀根檔案系統、volume；登入／設定 CRUD／金鑰遮罩與容器重啟後保存 |
| Kubernetes 封裝 | `deploy/k8s/` | Kustomize base與指定image overlay可渲染；kubeconform對1.37 resource schema嚴格驗證；單副本Recreate、PVC、Secret參照、probes |
| CI/CD 專案 | `.github/workflows/`、`scripts/` | actionlint語法驗證；測試、建置、容器smoke與持久化、需明確啟用的GHCR發布／Windows建置、手動production CD及固定映像部署腳本已提供 |

自動測試命令：

```bash
.venv/bin/python -m pytest -q tests
node --check web/static/app.js
.venv/bin/python -m compileall -q bot.py check.py history_archive.py image_generation.py tts.py desktop_entry.py desktop providers web scripts
```

桌面擴充前的容器測試結果為 **92 passed**；加入桌面版、Claude、生圖選用與依賴檢查後，Linux Python 3.13 與 Windows Python 3.12 完整回歸皆為 **219 passed**。涵蓋一次性桌面登入、OS instance lock、無終端視窗的日誌、檔案輪替、子 Bot 路徑與程序生命週期，以及生圖啟停與服務檢查。TestClient 有一項上游棄用提醒，不影響測試結果；容器與 CI 使用 Python 3.12。EXE 自測 JSON 隨 `dist/` 交付。

真實瀏覽器另外驗證：多 Provider、參數限制、API Key 更新及清除、失敗連線／試聊錯誤顯示、預設模型選擇、system prompt、ComfyUI/TTS 設定與日誌頁；API Key、Token 未寫入瀏覽器 localStorage。頁面截圖保存在 `docs/assets/`。

桌面版有 18 項 Chromium 模擬 API 檢查：桌面自動登入／AppData、Discord 身分與邀請、Ollama IP 補址、Claude 原生設定及不送 temperature、多 Provider 與金鑰清除、一鍵儲存啟動、離線與空模型不顯示就緒、忽略過時連線測試、390px 排版、一般 Web 登入、沒有 JavaScript 錯誤，以及生圖開關／四欄必填／逐項缺少依賴／過時結果／可暫存但啟動受後端阻擋／舊設定相容性。這些檢查不等於已使用真實 Anthropic Key 完成付費模型驗收。

生圖檢查只確認 ComfyUI 可達、指定模型出現在節點選單中、固定 workflow 所需輸入欄位相容；不會驗證模型檔案完整性、GPU 顯存、角色相似度或實際生成品質。未通過時不啟動已開生圖的 Bot；關閉生圖時不連接服務，舊房間已開啟的生圖狀態也不會繼續觸發圖片。重新啟動預檢失敗時不會先停止原本的 Bot。

Windows EXE 位於 `dist/`；另有包含說明及授權的 ZIP、`SHA256SUMS.txt`、兩種 JSON 自測結果與 `bundle-verification.json`。EXE 的四項自測包含：一次性登入、打包靜態資源、經 RuntimeManager 啟動自身 EXE 的 Bot 子程序與日誌讀取、隔離設定 CRUD。Bot 分支另確認資料不寫進打包暫存目錄、FFmpeg 已內含。

加入生圖選用與依賴檢查的新版 EXE 為 62,488,653 bytes（約 59.6 MiB），改用已核對 SHA-256 的 FFmpeg 8.1.2 Essentials build。交付 EXE／ZIP 的校驗值與 ZIP 完整性均通過，並已更新 Windows Downloads 的交付資料夾。

曾在前版 EXE 查到 Opus DLL 漏包，因此新規格明確收集 `discord/bin/libopus-0.x64.dll` 與其 COPYING 授權；建置不只檢查 archive 有檔案，還要求打包後 Bot 入口實際載入 DLL、建立 Encoder、編碼靜音 PCM 並得到非空 Opus 封包。這是離線編碼檢查，仍不代表已連上真實 Discord 語音頻道播放。

新版打包後實測 `opus_loaded: true`、`opus_encoded_bytes: 96`，獨立 archive 檢查亦為 `ok: true`。另外以本次自測程序的環境刻意保留非空 ComfyUI URL、設定 `IMAGE_SERVICE_ENABLED=false`，EXE 回報生圖關閉且未建立 image client；結果另存 `image-optout-self-test.json`，沒有呼叫任何生圖服務。新版從 Downloads 開啟原生 WebView2 與正常關閉的測試退出碼為 0。

已從 Windows 使用者的 Downloads 交付資料夾實際開啟原生 WebView2 視窗，自動登入並正常關閉，退出碼為 0。實機畫面含本機使用者資料路徑，不包含於公開文件資源。另外檢查 EXE archive：包含 `providers.anthropic`，內嵌 `app.js` 與最終來源 SHA-256 相同，包含 Claude API 預設與授權文件，不含 `.env`。

Kubernetes 驗證是 manifests/schema及容器層驗證，未在正式 Kubernetes 叢集 rollout。GitHub workflow 的遠端執行與發布狀態以該 repository 的實際 Actions 結果為準；本紀錄不把本機檢查視為已成功推送 GHCR 或公開發行 EXE。容器發布需要 `vars.PUBLISH_CONTAINER == 'true'`；Windows 建置需要 `vars.ENABLE_WINDOWS_BUILD == 'true'` 或手動 `workflow_dispatch`。正式 CD 另需設定 production environment 與部署憑證。真正 Discord gateway、付費模型、ComfyUI生成及語音播放需由部署者填入自身憑證與可用服務後做整合驗收；本次未以測試操作使用使用者的正式 Discord Token 或付費 API Key。
