from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI


PROJECT_DIR = Path(__file__).resolve().parent
INPUT_DIR = PROJECT_DIR / "input"
INPUT_FILE = INPUT_DIR / "meeting.m4a"
OUTPUT_DIR = PROJECT_DIR / "output"
CHUNKS_DIR = OUTPUT_DIR / "chunks"

MODEL = "gpt-4o-transcribe-diarize"
RESPONSE_FORMAT = "diarized_json"
CHUNKING_STRATEGY = "auto"

CHUNK_SECONDS = 15 * 60
OPUS_BITRATE = "24k"
MAX_UPLOAD_BYTES = 24 * 1024 * 1024

ProgressCallback = Callable[[str], None]


class UserFacingError(Exception):
    pass


def die(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def find_ffmpeg() -> str:
    candidates: list[str | None] = [
        os.environ.get("FFMPEG_PATH"),
        shutil.which("ffmpeg"),
        str(PROJECT_DIR / "ffmpeg.exe"),
        str(PROJECT_DIR / "tools" / "ffmpeg.exe"),
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\Youtube Downloader HD\ffmpeg.exe",
    ]

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate

    raise UserFacingError(
        "ffmpeg was not found. Install ffmpeg or set FFMPEG_PATH, for example: "
        "$env:FFMPEG_PATH='C:\\path\\to\\ffmpeg.exe'"
    )


def check_prerequisites(audio_path: Path, require_api_key: bool = True) -> str:
    if not audio_path.is_file():
        raise UserFacingError(f"Audio file not found: {audio_path}")

    ffmpeg = find_ffmpeg()

    if require_api_key and not os.environ.get("OPENAI_API_KEY"):
        raise UserFacingError(
            "OPENAI_API_KEY was not found. Set it in PowerShell, for example: "
            "$env:OPENAI_API_KEY='your-api-key'"
        )

    return ffmpeg


def clean_previous_outputs() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    CHUNKS_DIR.mkdir(exist_ok=True)

    for pattern in ("raw_chunk_*.json", "transcript.txt", "transcript_merged_raw.json"):
        for path in OUTPUT_DIR.glob(pattern):
            path.unlink()

    for path in CHUNKS_DIR.glob("chunk_*.webm"):
        path.unlink()


def run_ffmpeg(ffmpeg: str, audio_path: Path, progress: ProgressCallback = print) -> list[Path]:
    progress("Compressing and splitting audio into webm/opus chunks...")

    output_pattern = CHUNKS_DIR / "chunk_%03d.webm"
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(audio_path),
        "-vn",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libopus",
        "-b:a",
        OPUS_BITRATE,
        "-application",
        "voip",
        "-f",
        "segment",
        "-segment_time",
        str(CHUNK_SECONDS),
        "-reset_timestamps",
        "1",
        str(output_pattern),
    ]

    try:
        subprocess.run(command, check=True, cwd=PROJECT_DIR)
    except FileNotFoundError:
        raise UserFacingError(f"Could not run ffmpeg: {ffmpeg}") from None
    except subprocess.CalledProcessError as exc:
        raise UserFacingError(f"ffmpeg failed with exit code {exc.returncode}") from exc

    chunks = sorted(CHUNKS_DIR.glob("chunk_*.webm"))
    if not chunks:
        raise UserFacingError("ffmpeg did not produce any audio chunks.")

    oversized = [p for p in chunks if p.stat().st_size >= MAX_UPLOAD_BYTES]
    if oversized:
        details = ", ".join(f"{p.name}={p.stat().st_size / 1024 / 1024:.1f}MB" for p in oversized)
        raise UserFacingError(
            f"Some chunks are over the safe 24MB upload size: {details}. "
            "Lower OPUS_BITRATE, for example to '16k', and run again."
        )

    for path in chunks:
        progress(f"Chunk ready: {path.name} ({path.stat().st_size / 1024 / 1024:.2f} MB)")

    return chunks


def to_plain_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    raise UserFacingError(f"API response could not be converted to JSON: {type(value)!r}")


def transcribe_chunk(
    client: OpenAI,
    chunk_path: Path,
    index: int,
    total: int,
    progress: ProgressCallback = print,
) -> dict[str, Any]:
    progress(f"Transcribing chunk {index + 1}/{total}: {chunk_path.name}")
    try:
        with chunk_path.open("rb") as audio_file:
            result = client.audio.transcriptions.create(
                model=MODEL,
                file=audio_file,
                response_format=RESPONSE_FORMAT,
                chunking_strategy=CHUNKING_STRATEGY,
            )
    except Exception as exc:
        raise UserFacingError(f"API call failed for {chunk_path.name}: {exc}") from exc

    data = to_plain_dict(result)
    raw_path = OUTPUT_DIR / f"raw_chunk_{index:03d}.json"
    raw_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def seconds_to_timestamp(value: Any) -> str:
    try:
        total = float(value)
    except (TypeError, ValueError):
        return "??:??:??.???"

    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    seconds = total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def normalize_speaker(raw: Any, speaker_map: dict[str, str]) -> str:
    key = str(raw or "Unknown").strip() or "Unknown"
    if key not in speaker_map:
        speaker_map[key] = f"Speaker {chr(ord('A') + len(speaker_map))}"
    return speaker_map[key]


def extract_segments(chunk_data: dict[str, Any]) -> list[dict[str, Any]]:
    segments = chunk_data.get("segments")
    if isinstance(segments, list):
        return segments

    text = str(chunk_data.get("text", "")).strip()
    if text:
        return [{"start": 0, "end": 0, "speaker": "Unknown", "text": text}]

    return []


def merge_chunks(raw_chunks: list[dict[str, Any]]) -> dict[str, Any]:
    speaker_map: dict[str, str] = {}
    merged_segments: list[dict[str, Any]] = []

    for chunk_index, chunk_data in enumerate(raw_chunks):
        offset = chunk_index * CHUNK_SECONDS
        for segment in extract_segments(chunk_data):
            start = float(segment.get("start") or 0) + offset
            end = float(segment.get("end") or start) + offset
            raw_speaker = segment.get("speaker", "Unknown")
            speaker = normalize_speaker(raw_speaker, speaker_map)

            merged = dict(segment)
            merged["chunk_index"] = chunk_index
            merged["start"] = start
            merged["end"] = end
            merged["raw_speaker"] = raw_speaker
            merged["speaker"] = speaker
            merged["text"] = str(segment.get("text", "")).strip()
            merged_segments.append(merged)

    merged_segments.sort(key=lambda item: (item.get("start", 0), item.get("end", 0)))
    return {
        "model": MODEL,
        "response_format": RESPONSE_FORMAT,
        "chunking_strategy": CHUNKING_STRATEGY,
        "chunk_seconds": CHUNK_SECONDS,
        "speaker_map": speaker_map,
        "segments": merged_segments,
        "raw_chunks": raw_chunks,
    }


def write_outputs(merged: dict[str, Any]) -> dict[str, Path]:
    merged_raw_path = OUTPUT_DIR / "transcript_merged_raw.json"
    txt_path = OUTPUT_DIR / "transcript.txt"

    merged_raw_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("講者對照表")
    lines.append("=" * 40)
    if merged["speaker_map"]:
        for raw_speaker, display_name in merged["speaker_map"].items():
            lines.append(f"{display_name} = {raw_speaker}")
    else:
        lines.append("尚未辨識到講者")

    lines.append("")
    lines.append("逐字稿")
    lines.append("=" * 40)

    for segment in merged["segments"]:
        start = seconds_to_timestamp(segment.get("start"))
        end = seconds_to_timestamp(segment.get("end"))
        speaker = segment.get("speaker", "Speaker ?")
        text = segment.get("text", "")
        lines.append(f"[{start} - {end}] {speaker}: {text}")

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"txt": txt_path, "json": merged_raw_path}


def transcribe_file(
    audio_path: Path = INPUT_FILE,
    split_only: bool = False,
    progress: ProgressCallback = print,
) -> dict[str, Path]:
    audio_path = Path(audio_path)
    ffmpeg = check_prerequisites(audio_path, require_api_key=not split_only)
    clean_previous_outputs()
    chunks = run_ffmpeg(ffmpeg, audio_path, progress)

    if split_only:
        progress("Split-only test finished. OpenAI API was not called.")
        return {}

    client = OpenAI()
    raw_chunks = [
        transcribe_chunk(client, chunk, index, len(chunks), progress)
        for index, chunk in enumerate(chunks)
    ]
    progress("Merging transcript segments...")
    merged = merge_chunks(raw_chunks)
    paths = write_outputs(merged)
    progress("Done.")
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local meeting audio diarized transcription tool")
    parser.add_argument(
        "--split-only",
        action="store_true",
        help="Only run ffmpeg splitting. Useful for testing ffmpeg without calling OpenAI.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=INPUT_FILE,
        help="Audio file to transcribe. Default: input/meeting.m4a",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        paths = transcribe_file(args.input, split_only=args.split_only)
        if paths:
            print(f"Transcript: {paths['txt']}")
            print(f"Merged JSON: {paths['json']}")
    except UserFacingError as exc:
        die(str(exc))


if __name__ == "__main__":
    main()
