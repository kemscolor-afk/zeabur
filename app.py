from __future__ import annotations

import os
import smtplib
import threading
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from transcribe_meeting import INPUT_DIR, OUTPUT_DIR, UserFacingError, transcribe_file


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_BYTES", 2 * 1024 * 1024 * 1024))

UPLOAD_PATH = INPUT_DIR / "uploaded_audio"
ALLOWED_SUFFIXES = {".m4a", ".mp3", ".wav", ".webm", ".mp4", ".aac", ".flac", ".ogg"}

INPUT_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

job: dict[str, Any] = {
    "status": "idle",
    "phase": "idle",
    "message": "等待上傳音檔",
    "logs": [],
    "started_at": None,
    "finished_at": None,
    "error": None,
    "email": None,
    "email_status": None,
}
job_lock = threading.Lock()


def update_job(**updates: Any) -> None:
    with job_lock:
        job.update(updates)


def add_log(message: str) -> None:
    with job_lock:
        job["message"] = message
        job["logs"].append(message)
        job["logs"] = job["logs"][-250:]


def snapshot_job() -> dict[str, Any]:
    with job_lock:
        return dict(job, logs=list(job["logs"]))


def reset_job(email: str | None = None) -> None:
    with job_lock:
        job.update(
            {
                "status": "idle",
                "phase": "idle",
                "message": "等待上傳音檔",
                "logs": [],
                "started_at": None,
                "finished_at": None,
                "error": None,
                "email": email,
                "email_status": None,
            }
        )


def smtp_is_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def send_result_email(recipient: str, paths: dict[str, Path]) -> None:
    if not smtp_is_configured():
        raise UserFacingError("Email is not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, and SMTP_FROM.")

    sender = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))

    message = EmailMessage()
    message["Subject"] = "AJI meeting transcript is ready"
    message["From"] = sender
    message["To"] = recipient
    message.set_content("你的會議逐字稿已完成，附件包含 transcript.txt 與 transcript_merged_raw.json。")

    for key, path in paths.items():
        if not path.is_file():
            continue
        maintype = "text" if key == "txt" else "application"
        subtype = "plain" if key == "txt" else "json"
        message.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )

    with smtplib.SMTP(host, port, timeout=60) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)


def run_transcription(audio_path: Path, email: str | None) -> None:
    update_job(status="running", phase="processing", started_at=time.time(), finished_at=None, error=None)
    try:
        paths = transcribe_file(audio_path=audio_path, progress=add_log)
        update_job(status="done", phase="done", message="轉譯完成", finished_at=time.time())

        if email:
            update_job(email_status="sending")
            add_log(f"正在寄送結果到 {email}...")
            try:
                send_result_email(email, paths)
                update_job(email_status="sent")
                add_log("Email 已寄出。")
            except Exception as exc:
                update_job(email_status="failed")
                add_log(f"Email 寄送失敗：{exc}")
    except UserFacingError as exc:
        update_job(status="error", phase="error", message=str(exc), error=str(exc), finished_at=time.time())
    except Exception as exc:
        update_job(status="error", phase="error", message=f"Unexpected error: {exc}", error=str(exc), finished_at=time.time())


@app.get("/")
def index() -> str:
    return render_template("index.html", email_enabled=smtp_is_configured())


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.get("/api/status")
def status():
    return jsonify(snapshot_job())


@app.post("/api/transcribe")
def transcribe():
    current = snapshot_job()
    if current["status"] == "running":
        return jsonify({"error": "已有轉譯工作正在執行，請稍候。"}), 409

    uploaded = request.files.get("audio")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "請先選擇音檔。"}), 400

    suffix = Path(uploaded.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return jsonify({"error": f"不支援的檔案格式：{suffix or '(無副檔名)'}"}), 400

    email = (request.form.get("email") or "").strip() or None

    safe_name = secure_filename(uploaded.filename) or f"meeting{suffix}"
    audio_path = UPLOAD_PATH.with_suffix(Path(safe_name).suffix)

    reset_job(email=email)
    update_job(status="uploading", phase="uploading", message="正在接收上傳音檔")
    uploaded.save(audio_path)
    add_log(f"已收到音檔：{uploaded.filename}")

    thread = threading.Thread(target=run_transcription, args=(audio_path, email), daemon=True)
    thread.start()

    return jsonify({"ok": True})


@app.get("/download/transcript")
def download_transcript():
    path = OUTPUT_DIR / "transcript.txt"
    if not path.is_file():
        return jsonify({"error": "transcript.txt 尚未產生。"}), 404
    return send_file(path, as_attachment=True, download_name="transcript.txt")


@app.get("/download/json")
def download_json():
    path = OUTPUT_DIR / "transcript_merged_raw.json"
    if not path.is_file():
        return jsonify({"error": "transcript_merged_raw.json 尚未產生。"}), 404
    return send_file(path, as_attachment=True, download_name="transcript_merged_raw.json")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
