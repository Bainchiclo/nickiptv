import requests
import sys
from pathlib import Path

TIMEOUT = 10  # seconds
VALID_CONTENT_TYPES = [
    "application/vnd.apple.mpegurl",  # .m3u8
    "application/x-mpegURL",           # .m3u8
    "video/mp4",
    "audio/mpeg",
    "video/ts",
    "video/x-flv",
]

def is_stream_playable(url: str) -> bool:
    """
    Check if a stream URL is likely playable in a media player.
    Checks HTTP status and content type.
    """
    try:
        # Try HEAD first to get content type quickly
        response = requests.head(url, timeout=TIMEOUT, allow_redirects=True)
        if response.status_code < 400:
            content_type = response.headers.get("Content-Type", "").split(";")[0]
            if content_type in VALID_CONTENT_TYPES:
                return True
    except requests.RequestException:
        pass

    # Fallback to GET if HEAD fails or doesn't provide content type
    try:
        response = requests.get(url, timeout=TIMEOUT, stream=True)
        if response.status_code < 400:
            content_type = response.headers.get("Content-Type", "").split(";")[0]
            return content_type in VALID_CONTENT_TYPES
    except requests.RequestException:
        return False

    return False


def filter_m3u_playlist(input_path: str, output_path: str):
    """
    Reads an .m3u or .m3u8 playlist, filters playable URLs,
    and writes a new playlist.
    """
    with open(input_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip() for line in f]

    output_lines = []
    buffer_tags = []

    for line in lines:
        if line.startswith("#"):
            buffer_tags.append(line)
        elif line.strip():
            url = line.strip()
            print(f"Checking: {url}")
            if is_stream_playable(url):
                print("  ✓ Playable")
                output_lines.extend(buffer_tags)
                output_lines.append(url)
            else:
                print("  ✗ Not playable")
            buffer_tags = []

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")

    print(f"\nSaved filtered playlist to: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python filter_m3u_playlist.py input.m3u output.m3u")
        sys.exit(1)

    input_m3u = sys.argv[1]
    output_m3u = sys.argv[2]

    if not Path(input_m3u).exists():
        print("Input file does not exist.")
        sys.exit(1)

    filter_m3u_playlist(input_m3u, output_m3u)
