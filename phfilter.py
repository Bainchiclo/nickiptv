import requests
import sys
from pathlib import Path

TIMEOUT = 10  # seconds


def is_stream_online(url: str) -> bool:
    """
    Check if a stream URL is reachable.
    Uses HEAD first, falls back to GET if needed.
    """
    try:
        response = requests.head(url, timeout=TIMEOUT, allow_redirects=True)
        if response.status_code < 400:
            return True
    except requests.RequestException:
        pass

    try:
        response = requests.get(url, timeout=TIMEOUT, stream=True)
        return response.status_code < 400
    except requests.RequestException:
        return False


def filter_m3u8(input_path: str, output_path: str):
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

            if is_stream_online(url):
                print("  ✓ Online")
                output_lines.extend(buffer_tags)
                output_lines.append(url)
            else:
                print("  ✗ Offline")

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
