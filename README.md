# aji-meeting-transcriber

本機與 Zeabur 都可用的會議錄音多講者逐字稿工具。

主要流程：

1. 上傳音檔
2. ffmpeg 壓縮並切成 15 分鐘以內的 `webm/opus`
3. 逐段呼叫 OpenAI transcription API
4. 合併時間軸
5. 輸出 `transcript.txt` 與 `transcript_merged_raw.json`
6. 可選擇完成後寄送到 Email

## 本機安裝

```powershell
cd D:\aji-meeting-transcriber
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

如果 ffmpeg 不在 PATH，可以設定：

```powershell
$env:FFMPEG_PATH="C:\Program Files (x86)\Youtube Downloader HD\ffmpeg.exe"
```

設定 OpenAI API key：

```powershell
$env:OPENAI_API_KEY="你的 API key"
```

啟動 Web：

```powershell
python .\app.py
```

打開：

```text
http://127.0.0.1:5000
```

## Zeabur 部署

此專案已包含：

- `Dockerfile`
- `.dockerignore`
- `requirements.txt`

Zeabur 用 GitHub 托管部署時，請在 Zeabur 專案環境變數設定：

```text
OPENAI_API_KEY=你的 OpenAI API key
PORT=8080
```

Dockerfile 會：

- 使用 `python:3.11-slim`
- 安裝 `ffmpeg`
- 安裝 Python 套件
- 用 `gunicorn app:app` 啟動
- listen `0.0.0.0`
- 使用 `${PORT:-8080}`
- timeout 設為 1800 秒

## Email 通知

人可以先離開頁面。只要 Zeabur container 沒被重啟，後端會繼續處理目前工作。

若想完成後自動寄出附件，請在 Zeabur 設定 SMTP 環境變數：

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=你的寄件信箱
SMTP_PASSWORD=你的 SMTP app password
SMTP_FROM=你的寄件信箱
```

使用頁面時，在「完成後寄送到 Email」欄位填入收件信箱。完成後會寄出：

- `transcript.txt`
- `transcript_merged_raw.json`

提醒：目前仍是個人使用的簡化版，沒有資料庫與佇列。如果 Zeabur 在處理途中重啟 container，進行中的工作會中斷。若要做到真正可靠的離線長任務，需要加資料庫、物件儲存與背景 worker。

## 命令列模式

也可以直接跑：

```powershell
python .\transcribe_meeting.py --input "D:\recordings\my-meeting.m4a"
```

只測試 ffmpeg 切片、不呼叫 OpenAI：

```powershell
python .\transcribe_meeting.py --split-only
```

## 輸出檔案

```text
output\raw_chunk_000.json
output\raw_chunk_001.json
output\transcript.txt
output\transcript_merged_raw.json
```

Zeabur container 的檔案系統是暫存的，建議用 Email 取回結果，或完成後立刻在頁面下載。
