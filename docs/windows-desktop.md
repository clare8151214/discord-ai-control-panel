# Windows 桌面版：下載後雙擊使用

桌面版把管理介面、Python、Discord Bot 和語音播放需要的 FFmpeg 包成
`DiscordAIControlPanel.exe`。使用者不需要安裝 Python、Node.js、Docker 或 Kubernetes，
也不必編輯 `.env`。Windows 桌面版和伺服器版共用同一套管理介面與 Provider 實作。

## 使用者的第一次設定

1. 下載 Windows x64 Release ZIP，解壓縮到任意資料夾。
2. 雙擊 `DiscordAIControlPanel.exe`。程式會開啟桌面視窗並自動登入本機管理介面。
3. 建立 Bot 設定並貼上 Discord Bot Token，按「驗證 Token 並取得邀請連結」。
   介面會自動取得 Application ID，不必手動查找，再按邀請連結加入伺服器。
4. 選擇模型後端。Ollama 填 `http://127.0.0.1:11434`，遠端主機填它的 IP 和連接埠；
   Claude、OpenAI、Gemini、Grok 等閉源服務則依對應 Provider 填入 API Key、API URL 和模型名稱。
5. 按「測試連線」讀取模型清單，再選模型並送出測試訊息。按「儲存」和「啟動」。
6. Bot 啟動並加入 Discord 後，即可私訊或用 `/chat`。
   多人頻道啟用 `/autochat enable` 後，@Bot 或回覆 Bot 訊息即可聊天。

Ollama、ComfyUI 及模型權重由各自的服務提供；EXE 不會內含或下載數十 GB 的模型。
遠端 API 請填入能從這台 Windows 連到的位址，例如 `http://192.168.1.100:11434`（請替換為自己的主機 IP）。
ComfyUI 若使用另一個 IP，請在生圖設定中單獨填寫。
使用者可建立多份 Bot 設定，各自連到不同的模型和伺服器。

## 選擇是否使用生圖

生圖預設關閉。不需要圖片時，只填 Discord 和聊天模型設定即可；不必安裝 ComfyUI、NoobAI、IP-Adapter 或 CLIP Vision。

需要圖片時，前往「圖片與語音」：

1. 開啟「我要使用生圖功能」。
2. 填寫 ComfyUI API 網址，以及 NoobAI checkpoint、IP-Adapter、CLIP Vision 的完整檔名。預設檔名與目前 NoobAI-XL 1.1 工作流程一致，可依服務端的實際檔名調整。
3. 按「檢查生圖服務與模型」。清單會分別確認 ComfyUI 服務、10 個必要節點，以及三個模型是否出現在相應節點的模型選單中。
4. 缺少模型、節點不相容、ComfyUI 未啟動或網路不通時，會列出未通過的項目。補齊後再檢查；也可先儲存設定。
5. 按「儲存並啟動」。後端會重新檢查，不會只憑前一次的綠色結果放行。若未通過，可以關閉生圖後啟動文字 Bot。
6. 在 Discord 的房間執行 `/image enable`；需要角色一致性時，再用 `/character card` 和 `/character reference`。

安裝位置指的是 **ComfyUI API 所在的電腦**。使用遠端 ComfyUI 時，執行 EXE 的電腦不需要本地 GPU 或安裝這些模型。權重通常放在 ComfyUI 的 `models/checkpoints`、`models/ipadapter`、`models/clip_vision`；IP-Adapter 自訂節點也須先安裝並重新啟動 ComfyUI。

目前使用固定的 NoobAI／SDXL 生圖工作流程，不表示所有 checkpoint 架構都能通用。檢查是只讀 GET，不會自動下載／安裝模型或執行生圖，也不會驗證權重是否損毀、GPU 顯存是否足夠；實際圖片生成仍需另外測試。

關閉生圖會保留已填網址、角色資料與核准基準圖，不再連接生圖服務；既有房間也會停止生圖。修改此開關或模型設定後，正在運行的 Bot 需重新啟動。舊版已填 ComfyUI URL 的設定會保留為開啟狀態，不會無聲改成關閉。

Claude 請選擇 **Claude／Anthropic** 原生後端，貼上 Anthropic API Key；
官方 Base URL 為 `https://api.anthropic.com/v1`。
介面可讀取模型清單，也可手動填入完整 Claude 模型 ID。
若經由 OpenRouter 或其他轉接服務使用 Claude，則應選該服務的 OpenAI 相容後端，
填入它自己的 API URL 和 API Key。

Windows 10／11 x64 必須具備 Microsoft Edge WebView2 Runtime。
大多數已更新的 Windows 已具備；若缺少，程式會顯示安裝提示與
[Microsoft 官方下載頁](https://developer.microsoft.com/microsoft-edge/webview2/)，
並提供瀏覽器管理介面的備援方式。EXE 未內嵌龐大的 WebView2 安裝包。

首次執行 one-file EXE 需要將程式解壓縮到系統暫存目錄，啟動可能稍慢。
目前 EXE 未申請程式碼簽章，Windows 可能顯示發行者未知。
請使用專案下載來源及 `SHA256SUMS.txt` 確認檔案。

## 設定放在哪裡

桌面版持久資料預設在：

```text
%LOCALAPPDATA%\DiscordAIControlPanel\
```

每份 Bot 的設定、SQLite 對話資料庫、角色圖片和日誌都存到此目錄底下，
與 EXE 的暫存解壓縮目錄分離。升級時用新 EXE 取代舊 EXE，設定和對話仍保留。
移動 EXE 不會搬走資料；換電腦時可在關閉程式後備份／還原此資料夾。
設定包含 Discord Token 和 API Key，備份應視為機密資料。

桌面服務只監聽 `127.0.0.1` 的隨機連接埠，啟動時產生一次性登入憑證，
不要求使用者記住管理員密碼。關閉桌面視窗會關閉其啟動的 Bot 與本機服務；
要讓 Bot 長期在線，請保持程式開啟，或使用 Docker／Kubernetes 伺服器版。

## 開發者：在 Windows 建置

需要 Windows x64、Python 3.12 及網路。由 PowerShell 在專案內執行：

```powershell
./scripts/build-windows.ps1
```

此指令會建立獨立虛擬環境、安裝依賴、執行完整 Python 測試、收集第三方授權、
建置 EXE，並實際啟動打包後的 HTTP／GUI 入口與 Bot 入口做自測。
若系統找不到 FFmpeg，會從 Gyan 下載固定的 8.1.2 Essentials build，並核對固定 SHA-256。
可用 `-DownloadFFmpeg` 強制採用該已核對的版本。

來源位於 WSL 時也可直接建置，所有中間產物會放在 Windows NTFS，避免 UNC 路徑限制。以下 `<user>` 請替換成自己的 WSL 使用者名稱，發行版與專案路徑也請依實際位置調整：

```powershell
& "\\wsl.localhost\Ubuntu\home\<user>\ollama-discord-bot\scripts\build-windows.ps1" `
    -Python "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
```

可用 `-FFmpegPath` 與 `-FFmpegLicense` 指定自己的 FFmpeg 二進位檔及原始授權。
`-SkipInstall` 可重用已準備好的建置環境；`-SkipTests` 僅略過 Python 測試，
不會略過實際 EXE 自測。建置預設位置：

```text
獨立 Python：%USERPROFILE%\.venvs\discord-ai-desktop-build
建置快取：  %LOCALAPPDATA%\DiscordAIControlPanelBuild
交付檔案：  專案\dist\DiscordAIControlPanel.exe
            專案\dist\DiscordAIControlPanel-Windows-x64.zip
            專案\dist\SHA256SUMS.txt
            專案\dist\desktop-self-test.json
            專案\dist\bot-self-test.json
            專案\dist\bundle-verification.json
```

ZIP 附上與 EXE 相同的授權文件、依賴 inventory、建置資訊和自測結果。
`packaging/DiscordAIControlPanel.spec` 明確只打包程式和靜態資源；
不複製 `.env`、`data/`、Token 或對話資料。

可獨立重跑無 Token／無模型的自測：

```powershell
$exe = ".\dist\DiscordAIControlPanel.exe"
Start-Process $exe -ArgumentList '--self-test --result-file "C:\Temp\desktop-result.json"' -Wait
Start-Process $exe -ArgumentList '--bot-self-test --result-file "C:\Temp\bot-result.json"' -Wait
Get-Content C:\Temp\desktop-result.json
Get-Content C:\Temp\bot-result.json
```

必須確認結果 JSON 具有 `"ok": true`。HTTP 自測涵蓋本機服務、一次性自動登入、
靜態資源、設定建立／更新／刪除；Bot 自測涵蓋打包的 Bot／Provider／圖片／語音模組
匯入及資料路徑。這些測試不會登入真實 Discord 或使用付費模型，
因此 Discord 登入和特定 API 的最終連線仍由使用者在 GUI 中測試。

Windows Bot 自測另會實際載入 Opus DLL、將一段靜音 PCM 編碼為 Opus，確認 `opus_loaded: true` 和 `opus_encoded_bytes > 0`。打包檢查也核對 DLL／授權文件、必要模組與靜態資源，避免只測到 Python 匯入成功卻漏了語音依賴。這仍不等於已完成真實 Discord 語音頻道連線驗收。

在有桌面的 Windows 上，可額外驗證真正的 WebView2 視窗能開啟及正常關閉：

```powershell
./packaging/smoke_gui.ps1 -Executable ./dist/DiscordAIControlPanel.exe
```

## CI 與發行

`.github/workflows/windows-release.yaml` 可由 PR、main、`v*` tag 或手動 dispatch 觸發，但 Windows 建置 job 預設不執行。
維護者須在 repository Actions variables 明確設定 `ENABLE_WINDOWS_BUILD=true`（`vars.ENABLE_WINDOWS_BUILD == 'true'`），或手動執行 `workflow_dispatch`，才會啟用 Windows runner。
runner 會執行完整 Python 測試、PyInstaller 打包及兩種 JSON 自測，
檢查 SHA-256 後上傳可下載的 workflow artifact。所有 GitHub Action 使用固定 commit SHA。
啟用後，`v*` tag 的成功建置可將已測試檔案附加到 GitHub draft release；draft 仍需維護者檢查後發佈。
一般 push 不會預設公開 EXE／ZIP；手動建置的 artifact 也不等於已公開發佈 Release。本機建置不會 push、建立 tag 或操作 GitHub Release。

容器發布使用另一個明確開關 `PUBLISH_CONTAINER=true`；其事件與權限條件見 [部署說明](deployment.md#github-actions-ci測試建置發布)。

維護者公開散布前，請檢查 `packaging/ThirdPartyNotices.txt` 和 ZIP 裡的
`licenses/`。標準 FFmpeg build 為 GPLv3，附上 LICENSE／README 並不等於履行
完整對應原始碼的提供義務；公開 binary release 必須以符合 GPLv3 的方式提供
該 FFmpeg build、啟用的函式庫及建置腳本的完整對應原始碼。
工作流程因此先建立 draft，讓維護者完成對應原始碼和授權檢查後再公開。

建置依據：[PyInstaller one-file 與資源規格](https://pyinstaller.org/en/stable/spec-files.html)、
[pywebview 官方打包指引](https://pywebview.flowrl.com/guide/freezing.html)、
[FFmpeg Windows build 資訊](https://www.gyan.dev/ffmpeg/builds/)。
