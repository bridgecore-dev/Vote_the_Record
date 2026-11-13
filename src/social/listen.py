"""Turn a video into something to read: a timestamped transcript of its speech, and still frames.

For ideas the owner sends (e.g. a screen recording of a reel): Whisper (faster-whisper, run locally; the audio
is never sent anywhere) writes transcript.txt, and ffmpeg saves a frame every few seconds for on-screen text.
A transcript is a tip, never a source: machine transcription gets names and numbers wrong.

    pip install -r requirements-listen.txt
    python src/social/listen.py VIDEO [--out DIR] [--every 3] [--model small]
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def ffmpeg() -> str:
    found = shutil.which("ffmpeg") or (shutil.which("C:/ffmpeg/bin/ffmpeg.exe"))
    if found:
        return found
    import imageio_ffmpeg  # requirements-listen.txt; a bundled ffmpeg where none is installed
    return imageio_ffmpeg.get_ffmpeg_exe()


def frames(video: Path, out: Path, every: float) -> int:
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg(), "-loglevel", "error", "-y", "-i", str(video), "-vf", f"fps=1/{every},scale=720:-2",
                    str(out / "frame-%03d.jpg")], check=True)
    return len(list(out.glob("frame-*.jpg")))


def transcript(video: Path, out: Path, model: str) -> str:
    import numpy as np
    from faster_whisper import WhisperModel  # requirements-listen.txt
    # decode with ffmpeg (16 kHz mono) rather than faster-whisper's own decoder, which breaks across PyAV versions
    pcm = subprocess.run([ffmpeg(), "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "s16le", "-"], check=True, capture_output=True).stdout
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    segments, info = WhisperModel(model, device="cpu", compute_type="int8").transcribe(audio)
    lines = [f"[{int(s.start // 60)}:{int(s.start % 60):02d}] {s.text.strip()}" for s in segments]
    text = f"(machine transcript, language {info.language}; check names and numbers)\n" + "\n".join(lines)
    (out / "transcript.txt").write_text(text + "\n", encoding="utf-8")
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video", type=Path)
    ap.add_argument("--out", type=Path, help="folder for transcript.txt and frames (default: next to the video)")
    ap.add_argument("--every", type=float, default=3, help="seconds between saved frames")
    ap.add_argument("--model", default="small", help="Whisper model: tiny, base, small, medium")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    out = args.out or args.video.with_suffix("")
    n = frames(args.video, out, args.every)
    print(transcript(args.video, out, args.model))
    print(f"\n{n} frame(s) in {out}")


if __name__ == "__main__":
    main()
