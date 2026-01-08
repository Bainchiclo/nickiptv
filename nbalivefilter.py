import requests
import sys
import subprocess
import time
from pathlib import Path

TIMEOUT = 10
VLC_TEST_DURATION = 5  # seconds

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}

HLS_TAGS = (
    "#EXTM3U",
    "#EXT-X-STREAM-INF",
    "#EXT-X-TARGETDURATION",
    "#EXT-X-MEDIA",
)


# ------------------ HLS PRE-VALIDATION ------------------

def validate_m3u8(url: str) -> bool:
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        if r.status_code >= 400:
            return False

        text = r.text[:4096]
        return "#EXTM3U" in text and any(tag in text for tag in HLS_TAGS)

    except requests.RequestException:
        return False


def validate_segment(url: str) -> bool:
    try:
        r = requests.get(url, timeout=TIMEOUT, stream=True, headers=HEADERS)
        if r.status_code >= 400:
            return False

        chunk = next(r.iter_content(chunk_size=188), None)
        return bool(chunk and chunk[0] == 0x47)

    except requests.RequestException:
        return False


def is_hls_candidate(url: str) -> bool:
    try:
        head = requests.head(
            url,
            timeout=TIMEOUT,
            allow_redirects=True,
            headers=HEADERS,
        )

        if head.status_code >= 400:
            return False

        ct = head.headers.get("Content-Type", "").lower()

        if ".m3u8" in url.lower() or "mpegurl" in ct:
            return validate_m3u8(url)

        return validate_segment(url)

    except requests.RequestException:
        return False


# ------------------ VLC VALIDATION ------------------

def validate_with_vlc(url: str) -> bool:
    """
    Uses VLC to actually attempt playback.
    This is the most accurate validation possible.
    """
    try:
        proc = subprocess.Popen(
            [
                "cvlc",
                "--no-video",
                "--quiet",
                "--network-caching=1000",
                "--play-and-exit",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        start = time.time()

        while time.time() - start < VLC_TEST_DURATION:
            if proc.poll() is not None:
                return proc.returncode == 0
            time.sleep(0.2)

        proc.terminate()
        return True  # VLC was able to play without crashing

    except FileNotFoundError:
        print("✗ VLC (cvlc) not found in PATH")
        sys.exit(1)

    except Exception:
        return False


def is_stream_playable(url: str) -> bool:
    """
    Two-stage validation:
    1. Fast HLS validation
    2. Real VLC playback test
    """
    if not is_hls_candidate(url):
        return False

    return validate_with_vlc(url)


# ------------------ M3U8 FILTER ------------------

def filter_m3u8(input_path: str, output_path: str):
    with open(input_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip() for line in f]

    output_lines = []
    buffer_tags = []

    for line in lines:
        if line == "#EXTM3U":
            output_lines.append(line)
            continue

        if line.startswith("#"):
            buffer_tags.append(line)
            continue

        if line.strip():
            url = line.strip()
            print(f"Testing in VLC: {url}")

            if is_stream_playable(url):
                print("  ✓ VLC playable")
                output_lines.extend(buffer_tags)
                output_lines.append(url)
            else:
                print("  ✗ VLC failed")

            buffer_tags = []

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    print(f"\nSaved VLC-verified playlist to: {output_path}")


# ------------------ ENTRY POINT ------------------

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python filter_m3u8.py input.m3u8 output.m3u8")
        sys.exit(1)

    input_m3u8 = sys.argv[1]
    output_m3u8 = sys.argv[2]

    if not Path(input_m3u8).exists():
        print("Input file does not exist.")
        sys.exit(1)

    filter_m3u8(input_m3u8, output_m3u8)
