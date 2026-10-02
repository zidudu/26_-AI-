"""Verify expected video metadata and fully decode every audio/video stream."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", required=True, help="e.g. 30 or 30000/1001")
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--no-audio", action="store_true", help="Expect no audio stream")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {"file": str(args.video.resolve()), "passed": False, "errors": []}
    errors = result["errors"]
    try:
        if args.width <= 0 or args.height <= 0 or args.frames <= 0:
            raise ValueError("Expected dimensions and frame count must be positive")
        fps = Fraction(args.fps)
        if fps <= 0:
            raise ValueError("Expected FPS must be positive")
        probe = subprocess.run([args.ffprobe, "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(args.video)], check=True, capture_output=True, text=True, encoding="utf-8")
        metadata = json.loads(probe.stdout)
        videos = [s for s in metadata["streams"] if s["codec_type"] == "video"]
        audios = [s for s in metadata["streams"] if s["codec_type"] == "audio"]
        result["metadata"] = metadata
        if len(videos) != 1:
            errors.append(f"Expected one video stream; found {len(videos)}")
        if videos:
            video = videos[0]
            for key, expected in [("width", args.width), ("height", args.height)]:
                if video.get(key) != expected:
                    errors.append(f"{key}: {video.get(key)} != {expected}")
            if Fraction(video.get("avg_frame_rate", "0/1")) != fps:
                errors.append("Unexpected average frame rate")
            if int(video.get("nb_read_frames", -1)) != args.frames:
                errors.append("Decoded frame count differs from expectation")
        if args.no_audio and audios:
            errors.append("Expected silent video without an audio stream")
        if not args.no_audio and not audios:
            errors.append("Expected an audio stream")
        expected_seconds = args.frames / float(fps)
        for audio in audios:
            if "duration" in audio and abs(float(audio["duration"]) - expected_seconds) > 0.12:
                errors.append("Audio duration differs by more than 0.12 seconds")
        decoded = subprocess.run([args.ffmpeg, "-hide_banner", "-v", "error", "-xerror", "-i", str(args.video), "-map", "0:v", "-map", "0:a?", "-c:v", "rawvideo", "-c:a", "pcm_s16le", "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        result["decode_exit_code"] = decoded.returncode
        if decoded.returncode:
            errors.append("Full decode failed: " + decoded.stderr[-3000:])
        result["bytes"] = args.video.stat().st_size
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, ZeroDivisionError) as exc:
        errors.append(str(exc))
    result["passed"] = not errors
    report = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
