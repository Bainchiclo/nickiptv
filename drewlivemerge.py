import requests
import re
import time
from datetime import datetime

playlist_urls = [
    "https://raw.githubusercontent.com/Bainchiclo/nickiptv/refs/heads/main/PH.m3u",
    "https://raw.githubusercontent.com/Bainchiclo/nickiptv/refs/heads/main/NBA%20HOMECOURT.m3u",
    "https://mc-5dugfxm3jo.bunny.run/playlist.m3u8",
    "https://mc-hda11sn95o.bunny.run/playlist.m3u8",
    "https://raw.githubusercontent.com/Bainchiclo/nickiptv/refs/heads/main/Roxiestreams.m3u8",
    "http://tvpass.org/playlist/m3u",
    "https://raw.githubusercontent.com/Bainchiclo/nickiptv/refs/heads/main/Pixelsports.m3u8"
] 

EPG_URL = "https://github.com/Drewski2423/DrewLive/raw/refs/heads/main/DrewLive.xml.gz"
OUTPUT_FILE = "kibz.m3u8"

def fetch_playlist(url, retries=3, timeout=30):
    headers = {"User-Agent": "Mozilla/5.0"}
    for attempt in range(1, retries + 1):
        try:
            print(f"Attempting to fetch {url} (try {attempt})...")
            res = requests.get(url, timeout=timeout, headers=headers)
            res.raise_for_status()
            print(f"✅ Successfully fetched {url}")
            return res.text.strip().splitlines()
        except Exception as e:
            print(f"❌ Attempt {attempt} failed for {url}: {e}")
            time.sleep(2)
    print(f"⚠️ Skipping {url} after {retries} failed attempts.")
    return []

def parse_playlist(lines, source_url="Unknown"):
    parsed_channels = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            extinf_line = line
            channel_headers = []
            i += 1
            while i < len(lines) and lines[i].strip().startswith("#") and not lines[i].strip().startswith("#EXTINF:"):
                channel_headers.append(lines[i].strip())
                i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines):
                url_line = lines[i].strip()
                if url_line and not url_line.startswith("#") and url_line != "*":
                    parsed_channels.append((extinf_line, tuple(channel_headers), url_line))
                else:
                    print(f"⚠️ Skipped entry in {source_url}. Invalid URL '{url_line}'.")
                i += 1
            else:
                i += 1
        else:
            i += 1
    print(f"✅ Parsed {len(parsed_channels)} valid channels from {source_url}.")
    return parsed_channels

def write_merged_playlist(all_channels):
    lines = [f'#EXTM3U url-tvg="{EPG_URL}"', ""]
    sortable_channels = []

    for extinf, headers, url in all_channels:
        group_match = re.search(r'group-title="([^"]+)"', extinf)
        group = group_match.group(1) if group_match else "Other"
        try:
            title = extinf.rsplit(',', 1)[1].strip()
        except IndexError:
            title = ""
        sortable_channels.append((group.lower(), title.lower(), extinf, headers, url))

    sorted_channels = sorted(sortable_channels)
    current_group = None
    total_channels_written = 0

    for group_lower, title_lower, extinf, headers, url in sorted_channels:
        group_match = re.search(r'group-title="([^"]+)"', extinf)
        actual_group_name = group_match.group(1) if group_match else "Other"

        if actual_group_name != current_group:
            if current_group is not None:
                lines.append("")
            lines.append(f'#EXTGRP:{actual_group_name}')
            current_group = actual_group_name

        lines.append(extinf)
        for hdr_line in headers:
            lines.append(hdr_line)
        lines.append(url)
        total_channels_written += 1

    if lines and lines[-1] == "":
        lines.pop()

    final_output_string = '\n'.join(lines) + '\n'

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(final_output_string)

    print(f"\n✅ Merged playlist written to {OUTPUT_FILE}.")
    print(f"📊 Total channels merged (including duplicates): {total_channels_written}.")
    print(f"📝 Total lines in output file: {len(final_output_string.splitlines())}.")

if __name__ == "__main__":
    print(f"Starting playlist merge at {datetime.now()}...")
    all_channels_list = []

    for url in playlist_urls:
        lines = fetch_playlist(url)
        if lines:
            parsed_channels = parse_playlist(lines, source_url=url)
            all_channels_list.extend(parsed_channels)

    write_merged_playlist(all_channels_list)
    print(f"Merging complete at {datetime.now()}.")
