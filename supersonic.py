import asyncio
import aiohttp
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

# ---------- CONFIG (ADJUSTED & REALISTIC) ----------

TIMEOUT = aiohttp.ClientTimeout(total=12)

MAX_CONCURRENCY = 80
MAX_HLS_DEPTH = 3

MIN_SPEED_KBPS = 250        # realistic HD threshold
MAX_TTFB = 4.0              # allow CDN warmup
SAMPLE_BYTES = 384_000      # read up to 384 KB
WARMUP_BYTES = 32_000       # ignore first 32 KB for speed

RETRIES = 2                 # retry slow streams once

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

BLOCKED_DOMAINS = {
    "amagi.tv",
    "ssai2-ads.api.leiniao.com",
}

# ---------- SPEED TEST (WARMED & REALISTIC) ----------

async def stream_is_fast(session, url, headers):
    for attempt in range(RETRIES):
        try:
            start = time.perf_counter()

            async with session.get(url, headers=headers) as r:
                if r.status >= 400:
                    return False

                first_byte_time = None
                speed_start_time = None
                total = 0
                measured = 0

                async for chunk in r.content.iter_chunked(8192):
                    now = time.perf_counter()

                    if first_byte_time is None:
                        first_byte_time = now

                    total += len(chunk)

                    # warmup period
                    if total < WARMUP_BYTES:
                        continue

                    if speed_start_time is None:
                        speed_start_time = now

                    measured += len(chunk)

                    if measured >= SAMPLE_BYTES:
                        break

                if not speed_start_time:
                    continue

                ttfb = first_byte_time - start
                duration = max(now - speed_start_time, 0.001)
                speed_kbps = (measured / 1024) / duration

                if ttfb <= MAX_TTFB and speed_kbps >= MIN_SPEED_KBPS:
                    return True

        except Exception:
            pass

        await asyncio.sleep(0.2)

    return False

# ---------- STREAM VALIDATION ----------

async def is_stream_fast(session, url, headers, depth=0):
    if depth > MAX_HLS_DEPTH:
        return False

    for d in BLOCKED_DOMAINS:
        if d in url:
            return False

    # Non-HLS stream
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

# ---------- MAIN ----------

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

    connector = aiohttp.TCPConnector(limit_per_host=15, ssl=False)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async with aiohttp.ClientSession(
        timeout=TIMEOUT,
        connector=connector,
        headers=DEFAULT_HEADERS,
    ) as session:

        tasks = [check_stream(semaphore, session, e) for e in entries]
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

    out = ["#EXTM3U"]
    for _, extinf, vlcopts, url in fast_entries:
        out.extend(extinf)
        out.extend(vlcopts)
        out.append(url)

    Path(output_path).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\nSaved FAST playlist to: {output_path}")

# ---------- CLI ----------

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python fast_filter.py input.m3u output.m3u")
        sys.exit(1)

    if not Path(sys.argv[1]).exists():
        print("Input file does not exist.")
        sys.exit(1)

    asyncio.run(filter_fast_streams(sys.argv[1], sys.argv[2]))
