# 通用 Discord AI 控制台

控制台將原本的 Discord Bot 包成可以自行設定的服務。每個部署由一位管理者管理，可建立多組 Bot 設定；每組需使用自己的 Discord Bot Token。這是自架管理工具，不是提供互不信任租戶共用的公開 SaaS；所有取得管理密碼的人都能管理該部署的全部 Bot。

Windows 不想安裝開發環境時，直接使用 [EXE 桌面版](windows-desktop.md)：雙擊、自動登入本機介面，三步完成設定。以下啟動命令適用於自行架設 Web 版。

## 本機啟動

需要 Python 3.12 或以上；語音頻道播放需安裝 ffmpeg。以下在專案目錄執行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export CONTROL_PANEL_PASSWORD='請換成至少12字元的獨立管理密碼'
.venv/bin/python -m web
```

瀏覽 `http://127.0.0.1:8080`，以設定的管理密碼登入。Windows 可在 PowerShell 使用：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:CONTROL_PANEL_PASSWORD = '請換成至少12字元的獨立管理密碼'
.\.venv\Scripts\python.exe -m web
```

控制台本身不需 Discord Token 即可啟動；只有按下某組 Bot 的「啟動」才會登入 Discord。儲存設定不會自動登入、同步指令或重啟既有 Bot。管理頁面登入有效 12 小時，控制台重啟會要求重新登入。

## 建立第一個 Bot

1. Discord Developer Portal 建立 Application，進入 Bot 頁面取得 Bot Token。
2. Bot 頁面開啟 Message Content Intent，管理頁面中也開啟同名設定。多人訊息需要此權限。
3. 在控制台新增 Bot，填名稱、Bot Token。Application ID 只用於產生邀請連結，不需要填 Client Secret。
4. 將 Bot 邀進伺服器，至少允許 View Channel、Send Messages、Read Message History、Attach Files；語音播放需 Connect、Speak。邀請應包含 `bot` 與 `applications.commands` scope。
5. 新增一個或多個模型 Provider，測試連線、選擇預設 Provider 與模型，儲存。
6. 如需確認實際生成，按「聊天測試」；雲端 Provider 的這個動作可能產生供應商用量費用。
7. 按「啟動」，日誌顯示「Discord 已登入」後才代表 Bot 完成登入。只看到程序 PID 或 starting 還不代表 Discord 上線。
8. 私訊可直接輸入；伺服器頻道用 `/autochat enable`，之後 @Bot 或回覆 Bot。也可使用 `/chat`。

「驗證 Token」會向 Discord 查詢 Bot 的公開身分，自動填入 Application ID 及產生邀請連結，不會替你重設 Token 或加入群組。服務快速預設可選本機／遠端 Ollama、OpenAI、Grok、DeepSeek、Gemini、Claude；Ollama 直接填 IP 會補上 `http://` 和 `11434`。按「儲存並啟動」可一次完成兩個動作；若只想保存設定，仍可按一般「儲存設定」。

Server/Guild ID 留空時註冊全域指令、不綁定特定伺服器。填入時另外同步該伺服器測試指令；新全域指令可能需要 Discord 客戶端刷新。

## 模型連線

| 類型 | API URL 範例 | 說明 |
|---|---|---|
| Ollama | `http://192.168.1.100:11434` | 讀取 `/api/tags`，呼叫 `/api/chat`；請替換為自己的主機 IP |
| Docker 連宿主 Ollama | `http://host.docker.internal:11434` | Compose 已設定 host-gateway；宿主須允許容器連入 |
| OpenAI 相容 | `https://api.example.com/v1` | 使用模型清單與 Chat Completions；填完整 API root |
| Grok | `https://api.x.ai/v1` | 類型選 OpenAI 相容，填 xAI API Key 與帳號可用模型 |
| DeepSeek | `https://api.deepseek.com` | 類型選 OpenAI 相容，依官方 API root 設定 |
| Gemini 原生 | `https://generativelanguage.googleapis.com/v1beta` | 類型選 Gemini，使用 `generateContent` |
| Gemini 相容 | `https://generativelanguage.googleapis.com/v1beta/openai` | 類型選 OpenAI 相容 |
| Claude／Anthropic 原生 | `https://api.anthropic.com/v1` | 類型選 Claude／Anthropic，使用 Messages API 與 Anthropic API Key |

`localhost` 指執行控制台／Bot 的系統：在容器或 Kubernetes Pod 內不代表你的 Windows 電腦。Kubernetes 應填區網可路由 IP、DNS 或叢集中的 Ollama Service 名稱。

API URL 只允許 HTTP/HTTPS，不接受把帳密或 API Key 放在 URL。金鑰用獨立欄位填入。Provider ID 在同一 Bot 內不可重複；可建立兩個 Ollama Provider 連不同主機，也可同時設定本地與雲端模型。

手填模型清單適用於供應商無法完整列出模型或只希望公開部分模型的情況。「測試連線」仍會請求模型清單端點確認認證／網路，並非只看手填值；供應商不支援列模型時可能回報失敗，但可再用聊天測試驗證。模型名稱與權限以供應商實際提供為準，相容 API 不保證支援所有廠商專屬參數。

OpenAI 相容 Provider 的進階欄位可選 `max_tokens` 或 `max_completion_tokens`，也可關閉送出 temperature，以配合不同 API。`max_completion_tokens` 同時計入推理與可見回答，預設 768 tokens 可能不足以產生文字，可提高回答上限與 context 預算後再測試。OpenAI Docs 說明 `max_tokens` 已淘汰且不相容 o-series，因此使用相關模型時應選 `max_completion_tokens`；是否接受 temperature 仍以所選模型的官方規格為準。[官方 Chat Completions 參數](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create)

Claude 請直接選專用預設，不必轉成 OpenAI 相容 API。API Key 填 Anthropic Console 建立的金鑰，按測試連線取得帳號可用模型，或手動填入確切模型 ID；Claude 網頁版訂閱與 API 用量分開。對話記憶與 system prompt 會轉成 Anthropic Messages 格式；為相容不同代模型，預設不送可選的 temperature 參數。這裡使用文字聊天模式，不會自動啟用工具或延伸思考。

Discord `/models` 與 `/model` 使用同一份 Provider 清單，選擇其他 Provider 的模型會連同後端一起切換。現有 Bot 只傳送文字訊息給聊天模型，不因模型宣告 vision/tools 就會自動讀聊天附件或執行終端機。圖片生成仍透過 ComfyUI，雲端聊天內容會傳送到你選用的供應商。

## 記憶、角色、圖片與語音

控制台可以設定預設 system prompt、context 上限、回答 tokens、自動整理門檻、SQLite 原文保存天數（預設 7 天）、生圖服務開關、ComfyUI URL/checkpoint、IP-Adapter／CLIP Vision 檔名、TTS 預設聲音。修改後需重啟 Bot，執行中的版本會顯示設定已變更。

模型與 system prompt 的預設值只套用新房間；已有頻道保留 Discord `/model`、`/system` 設定，避免重啟覆蓋角色與劇情。房間仍使用 `/room_status`、`/room_compact`、`/room_reset`、`/room_delete_history` 管理；`/reset` 只影響自己的 `/chat` 記憶。

生圖使用 `/image enable`，角色一致性使用 `/character card`、`reference`、`approve_last`、`strength`。候選圖預設保存兩天，核准基準圖不自動刪除。遠端 ComfyUI 主機的圖片清理仍由它自己的排程負責；控制台不會安裝或刪除遠端模型。

生圖服務預設關閉，關閉時 Bot 不會連接 ComfyUI，也不影響文字、記憶與 TTS。開啟後按「檢查生圖服務與模型」會只讀確認 API 所在主機的服務、10 個必要節點與三個完整模型檔名。每次啟動／重新啟動已開生圖的 Bot 都會再次檢查；檢查失敗不會啟動，也不會因一次失敗的重新啟動檢查而先停止原本的 Bot。可先儲存未通過的設定，或關閉生圖後啟動。

這是模型選單與節點相容性檢查，不會實際生成圖片、安裝模型或驗證權重完整性。新主機需自行準備 NoobAI-XL／SDXL 工作流程所需資源；不同架構的生圖模型不能只改 checkpoint 名稱就保證相容。圖片清理的遠端排程也仍需獨立設定。

`/tts` 透過 Edge TTS 朗讀，可附 MP3 或在語音頻道播放；這項服務需要網路，並非本地聲音模型或語音辨識。原有 17 個根指令與其子指令完整保留，舊版指令表見主 README 下方。

## 設定與資料位置

```text
data/control-panel/                 # 可用 CONTROL_PANEL_DATA_DIR 覆寫
└── profiles/<隨機ID>/
    ├── config.json                 # Bot Token 與 API Key；POSIX 權限 0600
    ├── desired-running             # 記錄先前按下啟動；控制台重啟會恢復
    ├── state.json                  # 房間設定、摘要、近期記憶
    ├── history.sqlite3             # 此 Bot 的限期對話原文
    ├── characters/                 # 此 Bot 的角色圖
    └── bot.log                     # 遮罩金鑰、1MB輪替，保留2個備份
```

容器內資料是 `/data/control-panel`，由 Docker volume 或 Kubernetes PVC 保留。每組 Bot 使用獨立 subprocess、資料庫和狀態檔；同一部署拒絕同 Token 重複啟動。不要另外從 Windows 舊腳本啟動相同 Token 的 Bot，也不要同時對同一資料 volume 跑兩個控制台。

Token 與 API Key 不會由設定 API 回傳原文；儲存後欄位留空代表保留原值。API URL 或 Provider 類型更換後，請重新輸入金鑰。設定檔是權限受限的明文，不是加密金庫；備份 volume 前應按照憑證處理，勿提交 Git。公開 HTTPS 反向代理可加 `CONTROL_PANEL_SECURE_COOKIE=true`；預設只提供本機埠或 ClusterIP/port-forward。

停止 Bot 會清除恢復標記；控制台正常退出會停止子程序，但保留先前正在運行的 Bot 標記，下一次啟動會恢復。失敗的 Bot 在日誌顯示原因，修正後可手動啟動／重啟。刪除 Bot 前必須先停止；刪除會永久移除該 Bot 的設定、對話、圖片和日誌。

## 從原專案匯入

原本 `.env` 和 `data/state.json` 不會被 GUI 修改。若要以主機上的 `.env` 建立新的 GUI 設定，啟動控制台前設定：

```bash
export CONTROL_PANEL_ALLOW_LEGACY_IMPORT=true
```

登入後按「匯入舊設定」。只匯入連線與預設值，不匯入原頻道記憶、SQLite 和角色圖，也不自動啟動。匯入完成請檢查舊 IP 是否仍可連線，並在需要時更新。容器建置不包含 `.env`，所以正式部署預設不開放匯入。

## 驗證與擴充

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
node --check web/static/app.js
```

測試使用暫存資料目錄、假的 Bot 子程序與本機 HTTP server，不會登入真實 Discord 或呼叫付費模型。加入新模型協定可實作 `ChatProvider` 的 `start`、`close`、`probe`、`list_models`、`chat`，再加入 `providers/factory.py` 及表單類型。

API協定參考：[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[Gemini generateContent](https://ai.google.dev/api/generate-content)、[Gemini 模型清單](https://ai.google.dev/api/models)、[Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create)、[Anthropic 模型清單](https://platform.claude.com/docs/en/api/models/list)。Docker、Kubernetes、CI/CD 的操作見 [deployment.md](deployment.md)。
