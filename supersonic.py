import asyncio
import aiohttp
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

# ---------- CONFIG ----------

TIMEOUT = aiohttp.ClientTimeout(total=10)

MAX_CONCURRENCY = 100
MAX_HLS_DEPTH = 3

MIN_SPEED_KBPS = 400        # minimum throughput
MAX_TTFB = 1.5              # seconds
SAMPLE_BYTES = 256_000      # read max 256 KB

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

BLOCKED_DOMAINS = {
    "amagi.tv",
    "ssai2-ads.api.leiniao.com",
}

# ---------- SPEED TEST ----------

async def stream_is_fast(session, url, headers):
    try:
        start = time.perf_counter()

        async with session.get(url, headers=headers) as r:
            if r.status >= 400:
                return False

            first_chunk_time = None
            total = 0

            async for chunk in r.content.iter_chunked(8192):
                now = time.perf_counter()

                if first_chunk_time is None:
                    first_chunk_time = now

                total += len(chunk)

                if total >= SAMPLE_BYTES:
                    break

            if not first_chunk_time:
                return False

            ttfb = first_chunk_time - start
            duration = max(now - first_chunk_time, 0.001)
            speed_kbps = (total / 1024) / duration

            return (
                ttfb <= MAX_TTFB and
                speed_kbps >= MIN_SPEED_KBPS
            )

    except Exception:
        return False

# ---------- STREAM VALIDATION ----------

async def is_stream_fast(session, url, headers, depth=0):
    if depth > MAX_HLS_DEPTH:
        return False

    for d in BLOCKED_DOMAINS:
        if d in url:
            return False

    # Non-HLS streams → speed test directly
    if ".m3u8" not in url:
        return await stream_is_fast(session, url, headers)

    # HLS playlist
    try:
        async with session.get(url, headers=headers) as r:
            if r.status >= 400:
                return False
            text = await r.text()
    except Exception:
        return False

    if not text.startswith("#EXTM3U"):
        return False

    lines = text.splitlines()

    # Master playlist
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
            variant = lines[i + 1].strip()
            if not variant.startswith("#"):
                return await is_stream_fast(
                    session,
                    urljoin(url, variant),
                    headers,
                    depth + 1
                )
            return False

    # Media playlist → first segment
    segments = [l for l in lines if l and not l.startswith("#")]
    if not segments:
        return False

    segment_url = urljoin(url, segments[0])
    return await stream_is_fast(session, segment_url, headers)

# ---------- WORKER ----------

async def check_stream(semaphore, session, entry):
    extinf, vlcopts, url = entry
    headers = {}

    for opt in vlcopts:
        key, _, value = opt[len("#EXTVLCOPT:"):].partition("=")
        k = key.lower()
        if k == "http-referrer":
            headers["Referer"] = value
        elif k == "http-origin":
            headers["Origin"] = value
        elif k == "http-user-agent":
            headers["User-Agent"] = value

    async with semaphore:
        fast = await is_stream_fast(session, url, headers)

    title = ""
    if extinf:
        parts = extinf[0].split(",", 1)
        if len(parts) == 2:
            title = parts[1].strip()

    return fast, title, extinf, vlcopts, url

# ---------- MAIN LOGIC ----------

async def filter_fast_streams(input_path, output_path):
    lines = Path(input_path).read_text(
        encoding="utf-8", errors="ignore"
    ).splitlines()

    entries = []
    extinf, vlcopts = [], []

    for line in lines:
        if line.startswith("#EXTINF"):
            extinf = [line]
        elif line.startswith("#EXTVLCOPT"):
            vlcopts.append(line)
        elif line.startswith(("http://", "https://")):
            entries.append((extinf.copy(), vlcopts.copy(), line.strip()))
            extinf.clear()
            vlcopts.clear()

    connector = aiohttp.TCPConnector(
        limit_per_host=20,
        ssl=False,
    )

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async with aiohttp.ClientSession(
        timeout=TIMEOUT,
        connector=connector,
        headers=DEFAULT_HEADERS,
    ) as session:

        tasks = [
            check_stream(semaphore, session, e)
            for e in entries
        ]

        fast_entries = []

        for coro in asyncio.as_completed(tasks):
            fast, title, extinf, vlcopts, url = await coro
            if fast:
                print(f"✓ FAST: {title}")
                if extinf:
                    parts = extinf[0].split(",", 1)
                    extinf[0] = (
                        f'{parts[0]} group-title="Fast",{parts[1]}'
                        if len(parts) == 2
                        else f'{parts[0]} group-title="Fast"'
                    )
                fast_entries.append((title.lower(), extinf, vlcopts, url))
            else:
                print(f"✗ SLOW: {url}")

    fast_entries.sort(key=lambda x: x[0])

    output = ["#EXTM3U"]
    for _, extinf, vlcopts, url in fast_entries:
        output.extend(extinf)
        output.extend(vlcopts)
        output.append(url)

    Path(output_path).write_text(
        "\n".join(output) + "\n",
        encoding="utf-8"
    )

    print(f"\nSaved FAST playlist to: {output_path}")

# ---------- CLI ----------

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python fast_m3u_filter.py input.m3u output.m3u")
        sys.exit(1)

    if not Path(sys.argv[1]).exists():
        print("Input file does not exist.")
        sys.exit(1)

    asyncio.run(filter_fast_streams(sys.argv[1], sys.argv[2]))
