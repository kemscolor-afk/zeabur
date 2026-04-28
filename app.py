from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from transcribe_meeting import INPUT_DIR, OUTPUT_DIR, UserFacingError, transcribe_file


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024

UPLOAD_PATH = INPUT_DIR / "uploaded_audio"
ALLOWED_SUFFIXES = {".m4a", ".mp3", ".wav", ".webm", ".mp4", ".aac", ".flac", ".ogg"}

job: dict[str, Any] = {
    "status": "idle",
    "message": "等待上傳音檔",
    "logs": [],
    "started_at": None,
    "finished_at": None,
    "error": None,
}
job_lock = threading.Lock()


def update_job(**updates: Any) -> None:
    with job_lock:
        job.update(updates)


def add_log(message: str) -> None:
    with job_lock:
        job["message"] = message
        job["logs"].append(message)
        job["logs"] = job["logs"][-200:]


def snapshot_job() -> dict[str, Any]:
    with job_lock:
        return dict(job, logs=list(job["logs"]))


def reset_job() -> None:
    with job_lock:
        job.update(
            {
                "status": "idle",
                "message": "等待上傳音檔",
                "logs": [],
                "started_at": None,
                "finished_at": None,
                "error": None,
            }
        )


def run_transcription(audio_path: Path) -> None:
    update_job(status="running", started_at=time.time(), finished_at=None, error=None)
    try:
        transcribe_file(audio_path=audio_path, progress=add_log)
        update_job(status="done", message="轉譯完成", finished_at=time.time())
    except UserFacingError as exc:
        update_job(status="error", message=str(exc), error=str(exc), finished_at=time.time())
    except Exception as exc:
        update_job(status="error", message=f"Unexpected error: {exc}", error=str(exc), finished_at=time.time())


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/status")
def status():
    return jsonify(snapshot_job())


@app.post("/api/transcribe")
def transcribe():
    current = snapshot_job()
    if current["status"] == "running":
        return jsonify({"error": "已有轉譯工作正在執行"}), 409

    uploaded = request.files.get("audio")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "請先選擇音檔"}), 400

    suffix = Path(uploaded.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return jsonify({"error": f"不支援的檔案格式：{suffix or '(無副檔名)'}"}), 400

    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    safe_name = secure_filename(uploaded.filename) or f"meeting{suffix}"
    audio_path = UPLOAD_PATH.with_suffix(Path(safe_name).suffix)
    uploaded.save(audio_path)

    reset_job()
    add_log(f"已收到音檔：{uploaded.filename}")
    thread = threading.Thread(target=run_transcription, args=(audio_path,), daemon=True)
    thread.start()

    return jsonify({"ok": True})


@app.get("/download/transcript")
def download_transcript():
    path = OUTPUT_DIR / "transcript.txt"
    if not path.is_file():
        return jsonify({"error": "transcript.txt 尚未產生"}), 404
    return send_file(path, as_attachment=True, download_name="transcript.txt")


@app.get("/download/json")
def download_json():
    path = OUTPUT_DIR / "transcript_merged_raw.json"
    if not path.is_file():
        return jsonify({"error": "transcript_merged_raw.json 尚未產生"}), 404
    return send_file(path, as_attachment=True, download_name="transcript_merged_raw.json")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
