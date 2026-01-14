import re

def parse_m3u(file_path):
    """Parse an M3U file and return a list of entries as dicts with title and url."""
    entries = []
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('#EXTINF:'):
            # Extract title after last comma
            match = re.match(r'#EXTINF:-?\d+(.*),(.*)', line)
            if match:
                attributes = match.group(1).strip()
                title = match.group(2).strip()
            else:
                attributes = ""
                title = "Unknown"
            i += 1
            if i < len(lines):
                url = lines[i].strip()
            else:
                url = ""
            entries.append({'title': title, 'url': url, 'attributes': attributes})
        i += 1
    return entries

def stream_speed(url):
    """Try to determine speed from the URL. Assumes higher bitrate has bigger numbers in URL."""
    numbers = re.findall(r'\d+', url)
    if numbers:
        return max(int(n) for n in numbers)
    return 0

def make_extinf(entry, group_title="TCL+"):
    """Generate #EXTINF line with group-title."""
    attrs = entry['attributes']
    # If group-title exists, replace it, otherwise add it
    if 'group-title=' in attrs:
        attrs = re.sub(r'group-title=".*?"', f'group-title="{group_title}"', attrs)
    else:
        attrs = f'{attrs} group-title="{group_title}"'.strip()
    return f"#EXTINF:-1{(' ' + attrs) if attrs else ''},{entry['title']}"

def combine_playlists(file1, file2, output_file):
    entries1 = parse_m3u(file1)
    entries2 = parse_m3u(file2)
    
    combined = entries1 + entries2
    
    # Remove duplicates, keeping the fastest stream
    unique = {}
    for entry in combined:
        title = entry['title']
        speed = stream_speed(entry['url'])
        if title not in unique:
            unique[title] = entry
        else:
            existing_speed = stream_speed(unique[title]['url'])
            if speed > existing_speed:
                unique[title] = entry  # keep faster stream
    
    # Sort alphabetically by title
    sorted_entries = sorted(unique.values(), key=lambda x: x['title'].lower())
    
    # Write to new M3U file
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for entry in sorted_entries:
            f.write(f"{make_extinf(entry)}\n")
            f.write(f"{entry['url']}\n")
    
    print(f"Combined playlist saved to {output_file} with {len(sorted_entries)} unique entries.")

# Example usage:
combine_playlists("vidaa.m3u8", "supersonic.m3u8", "combine.m3u8")
