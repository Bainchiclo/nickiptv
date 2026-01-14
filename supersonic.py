import asyncio
import aiohttp
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

# ---------------- CONFIG ----------------

TIMEOUT = aiohttp.ClientTimeout(total=12)

MAX_CONCURRENCY = 80
MAX_HLS_DEPTH = 3

MIN_SPEED_KBPS = 150
MAX_TTFB = 4.0

SEGMENTS_TO_TEST = 2
SAMPLE_BYTES = 256_000
WARMUP_BYTES = 32_000
RETRIES = 2

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

BLOCKED_DOMAINS = {
    "amagi.tv",
    "ssai2-ads.api.leiniao.com",
}

# ---------------- QUALITY LABELING ----------------

def classify_speed(speed):
    if speed >= 1500:
        return "4K"
    if speed >= 600:
        return "FHD"
    if speed >= 300:
        return "HD"
    if speed >= 150:
        return "SD"
    return None

# ---------------- SPEED MEASUREMENT ----------------

async def measure_segment_speed(session, url, headers):
    try:
        start = time.perf_counter()
        async with session.get(url, headers=headers) as r:
            if r.status >= 400:
                return None

            first_byte = None
            speed_start = None
            total = 0
            measured = 0

            async for chunk in r.content.iter_chunked(8192):
                now = time.perf_counter()

                if first_byte is None:
                    first_byte = now

                total += len(chunk)

                if total < WARMUP_BYTES:
                    continue

                if speed_start is None:
                    speed_start = now

                measured += len(chunk)

                if measured >= SAMPLE_BYTES:
                    break

            if not speed_start or (first_byte - start) > MAX_TTFB:
                return None

            duration = max(now - speed_start, 0.001)
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

# ---------------- STREAM CHECK ----------------

async def check_stream_speed(session, url, headers, depth=0):
    if depth > MAX_HLS_DEPTH:
        return None

    if any(d in url for d in BLOCKED_DOMAINS):
        return None

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

    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
            return await check_stream_speed(
                session,
                urljoin(url, lines[i + 1].strip()),
                headers,
                depth + 1
            )

    segments = [l for l in lines if l and not l.startswith("#")]
    if len(segments) < SEGMENTS_TO_TEST:
        return None

    urls = [urljoin(url, s) for s in segments[:SEGMENTS_TO_TEST]]
    return await average_segment_speed(session, urls, headers)

# ---------------- WORKER ----------------

async def worker(semaphore, session, entry):
    extinf, vlcopts, url = entry
    headers = {}

    for opt in vlcopts:
        k, _, v = opt[len("#EXTVLCOPT:"):].partition("=")
        if k.lower() == "http-referrer":
            headers["Referer"] = v
        elif k.lower() == "http-origin":
            headers["Origin"] = v
        elif k.lower() == "http-user-agent":
            headers["User-Agent"] = v

    async with semaphore:
        speed = await check_stream_speed(session, url, headers)

    title = ""
    if extinf:
        parts = extinf[0].split(",", 1)
        if len(parts) == 2:
            title = parts[1].strip()

    return speed, title, extinf, vlcopts, url

# ---------------- MAIN ----------------

async def filter_playlist(input_path, output_path):
    lines = Path(input_path).read_text(encoding="utf-8", errors="ignore").splitlines()

    entries, extinf, vlcopts = [], [], []

    for line in lines:
        if line.startswith("#EXTINF"):
            extinf = [line]
        elif line.startswith("#EXTVLCOPT"):
            vlcopts.append(line)
        elif line.startswith(("http://", "https://")):
            entries.append((extinf.copy(), vlcopts.copy(), line.strip()))
            extinf.clear()
            vlcopts.clear()

    best = {}
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    connector = aiohttp.TCPConnector(limit_per_host=15, ssl=False)

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

            label = classify_speed(speed)
            if not label:
                continue

            key = f"{title.lower()}::{label}"

            if key in best and best[key]["speed"] >= speed:
                continue

            parts = extinf[0].split(",", 1)
            extinf[0] = (
                f'{parts[0]} group-title="{label}",'
                f'[{label}] {parts[1]}'
            )

            best[key] = {
                "title": title,
                "speed": speed,
                "extinf": extinf,
                "vlcopts": vlcopts,
                "url": url,
            }

            print(f"✓ {label}: {title} ({int(speed)} KB/s)")

    out = ["#EXTM3U"]
    for e in sorted(best.values(), key=lambda x: (x["title"].lower(), x["speed"])):
        out.extend(e["extinf"])
        out.extend(e["vlcopts"])
        out.append(e["url"])

    Path(output_path).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\nSaved playlist to: {output_path}")

# ---------------- CLI ----------------

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python filter.py input.m3u output.m3u")
        sys.exit(1)

    asyncio.run(filter_playlist(sys.argv[1], sys.argv[2]))
