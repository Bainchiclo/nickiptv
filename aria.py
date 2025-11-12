import requests
import re
import os

PLAYLIST_URLS = [
    "https://mc-o0of718lw0.bunny.run/playlist.m3u8"
]

OUTPUT_FILE = "AriaPlus.m3u8"

ALLOWED_GROUPS = [
    "CONVERGE", "Canada", "Japan", "New Zealand",
    "North Korea", "United Kingdom", "United States",
    "South Korea"
]

group_regex = re.compile(r'group-title="([^"]*)"')

def fetch_playlist(url):
    """Fetch playlist text and split into lines."""
    r = requests.get(url)
    r.raise_for_status()
    return r.text.splitlines()

def get_existing_urls(file_path):
    """Collect URLs already present in the local playlist."""
    urls = set()
    if not os.path.exists(file_path):
        return urls
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXTINF") and i + 1 < len(lines):
            urls.add(lines[i + 1].strip())
    return urls

def remap_group_title(line):
    """Prefix allowed group-titles with 'AriaPlus -', keep all other metadata intact."""
    match = group_regex.search(line)
    if not match:
        return None
    original_group = match.group(1)
    if original_group not in ALLOWED_GROUPS:
        return None

    new_line = re.sub(
        r'group-title="[^"]*"',
        f'group-title="AriaPlus - {original_group}"',
        line
    )
    return new_line

def process_playlist(lines, existing_urls):
    """Filter + remap channels, skipping already existing URLs."""
    output_lines = []
    skip_next = False
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith("#EXTINF:"):
            new_line = remap_group_title(line)
            if not new_line:
                skip_next = True
                continue
            # Check next line for URL
            if i + 1 < len(lines):
                url_line = lines[i + 1].strip()
                if url_line not in existing_urls:
                    output_lines.append(new_line)
                    output_lines.append(url_line)
                    existing_urls.add(url_line)
            skip_next = True
    return output_lines

def main():
    print("🔄 Updating AriaPlus playlist...")
    existing_urls = get_existing_urls(OUTPUT_FILE)
    new_entries = []

    for url in PLAYLIST_URLS:
        try:
            lines = fetch_playlist(url)
            new_entries.extend(process_playlist(lines, existing_urls))
        except Exception as e:
            print(f"⚠️ Failed to fetch {url}: {e}")

    if not os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")

    if new_entries:
        with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(new_entries) + "\n")
        print(f"✅ Added {len(new_entries)//2} new entries to {OUTPUT_FILE}")
    else:
        print("ℹ No new entries — playlist unchanged.")

if __name__ == "__main__":
    main()
