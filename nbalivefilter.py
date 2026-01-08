import requests
from urllib.parse import urljoin

TIMEOUT = 10
HLS_TAGS = ("#EXTM3U", "#EXT-X-STREAM-INF", "#EXT-X-TARGETDURATION", "#EXT-X-MEDIA")
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}


def is_hls_online(url: str, max_segments: int = 3) -> bool:
    """
    Check HLS streams online without VLC:
    1. Validate playlist
    2. Validate first few segments
    """
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        if r.status_code >= 400:
            return False

        text = r.text
        if "#EXTM3U" not in text:
            return False

        if not any(tag in text for tag in HLS_TAGS):
            return False

        # Extract first few segment URLs
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
        for segment_url in lines[:max_segments]:
            # Make absolute URL if needed
            segment_url = urljoin(url, segment_url)
            seg_r = requests.head(segment_url, timeout=TIMEOUT, allow_redirects=True, headers=HEADERS)
            if seg_r.status_code >= 400:
                return False

        return True

    except requests.RequestException:
        return False
