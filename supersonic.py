import asyncio
import aiohttp
import sys
from pathlib import Path
from urllib.parse import urljoin

TIMEOUT = aiohttp.ClientTimeout(total=8)
MIN_SEGMENT_SIZE = 20000
MAX_CONCURRENCY = 100
MAX_HLS_DEPTH = 3

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

BLOCKED_DOMAINS = {
    "amagi.tv",
    "ssai2-ads.api.leiniao.com",
}


# ---------- Network helpers ----------

async def head_ok(session, url, headers):
    try:
        async with session.head(url, headers=headers, allow_redirects=True) as r:
            return r.status < 400, r.headers.get("Content-Type", "").lower()
    except aiohttp.ClientError:
        return False, ""


async def stream_has_data(session, url, headers):
    try:
        async with session.get(url, headers=headers) as r:
            if r.status >= 400:
                return False

            total = 0
            async for chunk in r.content.iter_chunked(8192):
                total += len(chunk)
                if total >= MIN_SEGMENT_SIZE:
                    return True
            return False
    except aiohttp.ClientError:
        return False


# ---------- Stream validation ----------

async def is_stream_playable(session, url, headers, depth=0):
    if depth > MAX_HLS_DEPTH:
        return False

    for d in BLOCKED_DOMAINS:
        if d in url:
            return False

    ok, content_type = await head_ok(session, url, headers)
    if not ok:
        return False

    if ".m3u8" not in url and "mpegurl" not in content_type:
        return True

    try:
        async with session.get(url, headers=headers) as r:
            if r.status >= 400:
                return False
            text = await r.text()
    except aiohttp.ClientError:
        return False

    if not text.startswith("#EXTM3U"):
        return False

    lines = text.splitlines()

    # Master playlist
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
            variant = lines[i + 1].strip()
            if not variant.startswith("#"):
                return await is_stream_playable(
                    session,
                    urljoin(url, variant),
                    headers,
                    depth + 1,
                )
            return False

    # Media playlist → first segment
    segments = [l for l in lines if l and not l.startswith("#")]
    if not segments:
        return False

    return await stream_has_data(
        session,
        urljoin(url, segments[0]),
        headers,
    )


# ---------- Worker ----------

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
        playable = await is_stream_playable(session, url, headers)

    title = ""
    if extinf:
        parts = extinf[0].split(",", 1)
        if len(parts) == 2:
            title = parts[1].strip()

    return playable, title, extinf, vlcopts, url


# ---------- Playlist processing ----------

async def filter_m3u_playlist(input_path, output_path):
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

        playable_entries = []

        for coro in asyncio.as_completed(tasks):
            playable, title, extinf, vlcopts, url = await coro
            if playable:
                print(f"✓ {title}")
                if extinf:
                    parts = extinf[0].split(",", 1)
                    extinf[0] = (
                        f'{parts[0]} group-title="Supersonic",{parts[1]}'
                        if len(parts) == 2
                        else f'{parts[0]} group-title="Supersonic"'
                    )
                playable_entries.append((title.lower(), extinf, vlcopts, url))
            else:
                print(f"✗ {url}")

    playable_entries.sort(key=lambda x: x[0])

    out = ["#EXTM3U"]
    for _, extinf, vlcopts, url in playable_entries:
        out.extend(extinf)
        out.extend(vlcopts)
        out.append(url)

    Path(output_path).write_text(
        "\n".join(out) + "\n",
        encoding="utf-8",
    )

    print(f"\nSaved to {output_path}")


# ---------- CLI ----------

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python filter_m3u_playlist_async.py input.m3u output.m3u")
        sys.exit(1)

    if not Path(sys.argv[1]).exists():
        print("Input file does not exist.")
        sys.exit(1)

    asyncio.run(filter_m3u_playlist(sys.argv[1], sys.argv[2]))
