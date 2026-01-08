import requests
import sys
from pathlib import Path

TIMEOUT = 10

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


def validate_m3u8(url: str) -> bool:
    """Validate an HLS playlist by checking required tags."""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        if r.status_code >= 400:
            return False

        text = r.text[:4096]  # Read only first few KB
        if "#EXTM3U" not in text:
            return False

        return any(tag in text for tag in HLS_TAGS)

    except requests.RequestException:
        return False


def validate_segment(url: str) -> bool:
    """Validate an HLS media segment (TS/AAC/MP4)."""
    try:
        r = requests.get(
            url,
            timeout=TIMEOUT,
            stream=True,
            headers=HEADERS,
        )
        if r.status_code >= 400:
            return False

        chunk = next(r.iter_content(chunk_size=188), None)
        if not chunk:
            return False

        # MPEG-TS sync byte
        return chunk[0] == 0x47

    except requests.RequestException:
        return False


def is_hls_stream_valid(url: str) -> bool:
    """
    HLS-aware stream validation.
    Uses HEAD for detection and GET for content verification.
    """
    try:
        head = requests.head(
            url,
            timeout=TIMEOUT,
            allow_redirects=True,
            headers=HEADERS,
        )

        if head.status_code >= 400:
            return False

        content_type = head.headers.get("Content-Type", "").lower()

        # HLS playlist
        if ".m3u8" in url.lower() or "mpegurl" in content_type:
            return validate_m3u8(url)

        # Media segment
        return validate_segment(url)

    except requests.RequestException:
        return False


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
            print(f"Checking: {url}")

            if is_hls_stream_valid(url):
                print("  ✓ Valid HLS stream")
                output_lines.extend(buffer_tags)
                output_lines.append(url)
            else:
                print("  ✗ Invalid / offline")

            buffer_tags = []

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    print(f"\nSaved filtered playlist to: {output_path}")


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
