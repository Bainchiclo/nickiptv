import asyncio
import aiohttp
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

# ---------- CONFIG ----------

TIMEOUT = aiohttp.ClientTimeout(total=12)

MAX_CONCURRENCY = 80
MAX_HLS_DEPTH = 3

MIN_SPEED_KBPS = 150
MAX_TTFB = 4.0

SAMPLE_BYTES = 256_000
WARMUP_BYTES = 32_000
SEGMENTS_TO_TEST = 2
RETRIES = 2

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

BLOCKED_DOMAINS = {
    "amagi.tv",
    "ssai2-ads.api.leiniao.com",
}

# ---------- AUTO LABELING ----------

def classify_speed(speed_kbps):
    if speed_kbps >= 1500:
        return "4K"
    if speed_kbps >= 600:
        return "FHD"
    if speed_kbps >= 300:
        return "HD"
    if speed_kbps >= 150:
        return "SD"
    return None

# ---------- SPEED TEST ----------

async def measure_segment_speed(session, url, headers):
    try:
        start = time.perf_counter()

        async with session.get(url, headers=headers) as r:
            if r.status >= 400:
                return None

            first_byte_time = None
            speed_start_time = None
            total = 0
            measured = 0

            async for chunk in r.content.iter_chunked(8192):
                now = time.perf_counter()

                if first_byte_time is None:
                    first_byte_time = now

                total += len(chunk)

                if total < WARMUP_BYTES:
                    continue

                if speed_start_time is None:
                    speed_start_time = now

                measured += len(chunk)

                if measured >= SAMPLE_BYTES:
                    break

            if not speed_start_time:
                return None

            if first_byte_time - start > MAX_TTFB:
                return None

            duration = max(now - speed_start_time, 0.001)
            return (measured / 1024) / duration

    except Exception:
        return None

async def average_segment_speed(session, urls, headers):
    speeds = []

    for url in urls:
        for _ in range(RETRIES):
            s = await measure_segment_speed(session, url, headers)
            if s:
                speeds.append(s)
                break
            await asyncio.sleep(0.2)

    if len(speeds) < SEGMENTS_TO_TEST:
        return None

    return sum(speeds) / len(speeds)

# ---------- STREAM CHECK ----------

async def check_stream_speed(session, url, headers, depth=0):
    if depth > MAX_HLS_DEPTH:
        return None

    for d in BLOCKED_DOMAINS:
        if d in url:
            return None

    # Non-HLS
    if ".m3u8" not in url:
        return await average_segment_speed(session, [url], headers)

    try:
        async with session.get(url, headers=headers) as r:
            if r.status >= 400:
                return None
            text = await r.text()
    except Exception:
        return None

    if not text.startswith("#EXTM3U"):
        return None

    lines = text.splitlines()

    # Master playlist
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
            variant = lines[i + 1].strip()
            if not variant.startswith("#"):
                return await check_stream_speed(
                    session,
                    urljoin(url, variant),
                    headers,
                    depth + 1
                )
            return None

    segments = [l for l in lines if l and not l.startswith("#")]
    if len(segments) < SEGMENTS_TO_TEST:
        return None

    segment_urls = [urljoin(url, s) for s in segments[:SEGMENTS_TO_TEST]]
    return await average_segment_speed(session, segment_urls, headers)

# ---------- WORKER ----------

async def worker(semaphore, session, entry):
    extinf, vlcopts, url = entry
    headers = {}

    for opt in vlcopts:
        k, _, v = opt[len("#EXTVLCOPT:"):].partition("=")
        k = k.lower()
        if k == "http-referrer":
            headers["Referer"] = v
        elif k == "http-origin":
            headers["Origin"] = v
        elif k == "http-user-agent":
            headers["User-Agent"] = v

    async with semaphore:
        speed = await check_stream_speed(session, url, headers)

    title = ""
    if extinf:
        parts = extinf[0].split(",", 1)
        if len(parts) == 2:
            title = parts[1].strip()

    return speed, title, extinf, vlcopts, url

# ---------- MAIN ----------

async def filter_and_label(input_path, output_path):
    lines = Path(input_path).read_text(encoding="utf-8", errors="ignore").splitlines()

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

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    connector = aiohttp.TCPConnector(limit_per_host=15, ssl=False)

    best_by_title = {}

    async with aiohttp.ClientSession(
        timeout=TIMEOUT,
        connector=connector,
        headers=DEFAULT_HEADERS
    ) as session:

        tasks = [worker(semaphore, session, e) for e in entries]

        for coro in asyncio.as_completed(tasks):
            speed, title, extinf, vlcopts, url = await coro

            if not title or speed is None or speed < MIN_SPEED_KBPS:
                continue

            key = title.lower().strip()
            label = classify_speed(speed)

            if not label:
                continue

            # Keep only the fastest stream per title
            if key in best_by_title and best_by_title[key]["speed"] >= speed:
                continue

            if extinf:
                parts = extinf[0].split(",", 1)
                name = parts[1] if len(parts) == 2 else title
                extinf[0] = (
                    f'{parts[0]} group-title="{label}",'
                    f'[{label}] {name} ({int(speed)} KB/s)'
                )

            best_by_title[key] = {
                "speed": speed,
                "title": title,
                "extinf": extinf,
                "vlcopts": vlcopts,
                "url": url,
            }

            print(f"✓ {label}: {title} ({int(speed)} KB/s)")

    # Sort alphabetically
    final_entries = sorted(
        best_by_title.values(),
        key=lambda x: x["title"].lower()
    )

    out = ["#EXTM3U"]
    for e in final_entries:
        out.extend(e["extinf"])
        out.extend(e["vlcopts"])
        out.append(e["url"])

    Path(output_path).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\nSaved deduplicated FAST playlist to: {output_path}")

# ---------- CLI ----------

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python auto_label_filter.py input.m3u output.m3u")
        sys.exit(1)

    if not Path(sys.argv[1]).exists():
        print("Input file does not exist.")
        sys.exit(1)

    asyncio.run(filter_and_label(sys.argv[1], sys.argv[2]))
