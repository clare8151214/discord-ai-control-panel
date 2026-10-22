# Discord 使用方法

在控制台設定並啟動 Bot。日誌出現「Discord 已登入」才代表連上 Gateway，程序 PID 或「啟動中」不代表登入成功。全域指令同步後，Discord 客戶端可能需要刷新。

## 聊天與記憶

| 用法 | 記憶範圍 | 注意事項 |
|---|---|---|
| `/chat message:...` | 每人、每個頻道獨立的近期歷史 | 問答在所在頻道公開；不參與房間摘要或 SQLite 封存 |
| 私訊直接輸入 | 該私訊的房間記憶 | 自動摘要及 SQLite 封存 |
| 頻道 @Bot 或回覆 Bot | 同一房間共用記憶 | 管理員先執行 `/autochat enable`；不同頻道／討論串隔離 |

`/chat` 預設保留最近 40 則歷史訊息（使用者與 Bot 合計），用 `/reset` 清除。房間使用 `/room_compact` 整理摘要、`/room_reset` 清除活動上下文；SQLite 原文仍按設定天數刪除。這些指令不會刪除 Discord 平台上已發出的訊息。

## 指令

| 指令 | 用途 |
|---|---|
| `/chat`、`/reset`、`/status` | 個人對話、清除自己的近期歷史、查看房間設定 |
| `/providers`、`/provider` | 列出／切換模型服務 |
| `/models`、`/model` | 列出／切換模型 |
| `/system`、`/system_clear` | 設定／清除房間 system prompt |
| `/autochat enable`、`disable` | 開關多人模式；只有 @Bot 或回覆 Bot 觸發 |
| `/room_status` | 查看房間活動記憶與 SQLite 用量 |
| `/room_compact` | 摘要舊對話並保留近期情境 |
| `/room_reset` | 清除摘要及近期上下文，不立即刪除 SQLite 原文 |
| `/room_delete_history confirm:True` | 永久刪除該房間的活動記憶與 SQLite 原文 |
| `/image enable`、`disable`、`status` | 開關情境生圖、查看狀態 |
| `/character card` | 設定固定角色外觀 |
| `/character reference` | 上傳並核准固定角色基準圖 |
| `/character approve_last` | 核准上一張生成圖為基準圖 |
| `/character strength`、`status`、`clear` | 調整相似度、查詢／清除角色設定 |
| `/tts enable`、`disable` | 開關 MP3 朗讀及已連線的語音播放 |
| `/tts voice`、`mode` | 設定聲音與朗讀範圍 |
| `/tts join`、`leave` | 加入使用者所在語音頻道／離開 |

模型、提示詞、角色與房間設定指令預設限制具有「管理伺服器」權限的人，可在 GUI 調整。私訊使用者可管理自己的 scope。既有 slash command 結果目前是公開訊息，不能把「個人記憶」當成「回覆隱藏」。

## 生圖、角色與語音

先在控制台啟用生圖並通過依賴檢查，再在房間使用 `/image enable`。基準圖使用 NoobAI IP-Adapter，不表示聊天模型會自動辨識任意附件。核准圖不會被後續圖片自動覆蓋，也不因房間重設清除；`/character clear` 會刪除它。

TTS 使用線上 Edge TTS，並非離線語音模型或語音辨識。MP3 需要附件權限；語音播放需要 Connect／Speak、已加入語音頻道且開啟 TTS。「只朗讀引號台詞」模式遇到沒有引號的回答可能不出聲。

GUI 預設模型、system prompt 與聲音主要初始化新房間，已有房間保留自己的設定，請用 Discord 指令切換。修改 GUI 服務設定後需重啟 Bot。不要用舊腳本與 GUI 同時啟動同一個 Discord Token。
