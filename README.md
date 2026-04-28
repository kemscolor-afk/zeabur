# aji-meeting-transcriber

本機用的會議錄音多講者逐字稿工具。現在有兩種使用方式：

- Web 介面：拖入音檔，按「轉譯」，完成後下載 TXT / JSON。
- 命令列：沿用 `input/meeting.m4a` 或指定音檔路徑。

## 需求

- Windows
- Python 3.10 以上
- ffmpeg
- OpenAI API key

OpenAI audio API 單檔上傳限制是 25 MB。工具會先用 ffmpeg 壓縮成 `webm/opus`，切成 15 分鐘以內片段，並檢查每段小於 24 MB。

## 安裝

在 PowerShell 執行：

```powershell
cd D:\aji-meeting-transcriber
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 設定 ffmpeg

如果 `ffmpeg` 已經在 PATH 裡，可以略過。

如果你的電腦已有 `ffmpeg.exe`，但不在 PATH，可以在同一個 PowerShell 視窗設定：

```powershell
$env:FFMPEG_PATH="C:\Program Files (x86)\Youtube Downloader HD\ffmpeg.exe"
```

也可以把 `ffmpeg.exe` 放在：

```text
D:\aji-meeting-transcriber\ffmpeg.exe
```

## 設定 OpenAI API key

```powershell
$env:OPENAI_API_KEY="你的 API key"
```

## 使用 Web 介面

啟動本機 Web 服務：

```powershell
cd D:\aji-meeting-transcriber
.\.venv\Scripts\Activate.ps1
python .\app.py
```

打開瀏覽器：

```text
http://127.0.0.1:5000
```

把音檔拖進頁面，按「轉譯」。完成後可以下載：

```text
output\transcript.txt
output\transcript_merged_raw.json
```

## 使用命令列

把會議錄音放到：

```text
D:\aji-meeting-transcriber\input\meeting.m4a
```

執行：

```powershell
python .\transcribe_meeting.py
```

也可以指定音檔：

```powershell
python .\transcribe_meeting.py --input "D:\recordings\my-meeting.m4a"
```

## 只測試 ffmpeg 切片

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

## 常見錯誤

### 找不到 ffmpeg

請安裝 ffmpeg，或設定：

```powershell
$env:FFMPEG_PATH="C:\path\to\ffmpeg.exe"
```

### 找不到 OPENAI_API_KEY

請設定：

```powershell
$env:OPENAI_API_KEY="你的 API key"
```

### API 呼叫失敗

程式會顯示是哪個 chunk 失敗，以及 OpenAI SDK 回傳的錯誤訊息。通常需要檢查 API key、帳號額度、模型權限或網路連線。
