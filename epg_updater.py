import cloudscraper
import gzip
import os
import datetime
import xml.etree.ElementTree as ET
import re

# --- CONFIGURATION ---
CUTOFF_DATE = datetime.datetime(2026, 1, 10)
FILE_PATH = "epg.xml.gz"

# --- 1. Get Secrets ---
USERNAME = os.environ.get('CENTRA_USERNAME')
PASSWORD = os.environ.get('CENTRA_PASSWORD')

if not USERNAME or not PASSWORD:
    print("Error: CENTRA_USERNAME or CENTRA_PASSWORD not found.")
    exit(1)

URL = f"https://centra.ink/xmltv.php?username={USERNAME}&password={PASSWORD}&ext=.xml.gz"

# --- 2. Helper Functions ---
def clean_xml_data(raw_xml):
    """Aggressively removes illegal characters from XML"""
    # 1. Remove invalid XML control characters (ASCII 0-8, 11-12, 14-31)
    # XML 1.0 only allows \x09 (tab), \x0A (newline), \x0D (carriage return)
    raw_xml = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw_xml)
    
    # 2. Fix unescaped ampersands (e.g. "Tom & Jerry" -> "Tom &amp; Jerry")
    # Matches '&' that is NOT followed by an existing entity like 'amp;'
    raw_xml = re.sub(r'&(?!(?:amp|lt|gt|apos|quot|#\d+|#x[0-9a-fA-F]+);)', '&amp;', raw_xml)
    
    return raw_xml

def convert_to_ist(time_str):
    if not time_str: return ""
    try:
        dt_part = time_str.split()[0]
        dt = datetime.datetime.strptime(dt_part, '%Y%m%d%H%M%S')
        dt_ist = dt + datetime.timedelta(hours=5, minutes=30)
        return dt_ist.strftime('%Y%m%d%H%M%S') + " +0530"
    except Exception:
        return time_str

def keep_channel(name):
    n = name.lower()
    if "kayo" in n: return True
    if "fox" in n:
        if any(x in n for x in ['501', '502', '503', '504', '505', '506', '507', 'news', 'cricket', 'league', 'footy']):
            return True
    if "sky" in n and "sports" in n: return True
    if "tnt" in n and "sports" in n: return True
    return False

def get_date_object(time_str):
    try:
        return datetime.datetime.strptime(time_str[:14], '%Y%m%d%H%M%S')
    except:
        return None

# --- 3. Download ---
print("Downloading EPG with MAG200 Agent...")
scraper = cloudscraper.create_scraper()
stb_headers = {
    "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 4 rev: 2738 Mobile Safari/533.3",
    "Accept": "*/*",
    "Connection": "keep-alive"
}

try:
    response = scraper.get(URL, headers=stb_headers)
    response.raise_for_status()
    
    if response.content.startswith(b'\x1f\x8b'):
        xml_data = gzip.decompress(response.content).decode('utf-8', errors='replace')
    else:
        xml_data = response.text
        
except Exception as e:
    print(f"Download Error: {e}")
    exit(1)

# --- 4. Clean & Parse ---
print("Cleaning XML data...")
xml_data = clean_xml_data(xml_data)

print("Parsing XML...")
try:
    new_root = ET.fromstring(xml_data)
except ET.ParseError as e:
    print(f"Critical XML Error even after cleaning: {e}")
    # Last ditch attempt: wrap in a dummy root if structure is totally broken
    try:
        print("Attempting fallback parse...")
        new_root = ET.fromstring(f"<tv>{xml_data}</tv>")
    except:
        print("Failed. Saving debug file.")
        exit(1)

merged_programmes = {}
valid_channel_ids = set()
final_channels = {}

# Filter Channels
for channel in new_root.findall('channel'):
    cid = channel.get('id')
    name = channel.find('display-name').text or ""
    if keep_channel(name):
        valid_channel_ids.add(cid)
        final_channels[cid] = channel

# Filter Programmes
for p in new_root.findall('programme'):
    cid = p.get('channel')
    if cid in valid_channel_ids:
        p.set('start', convert_to_ist(p.get('start')))
        p.set('stop', convert_to_ist(p.get('stop')))
        merged_programmes[(cid, p.get('start'))] = p

# --- 5. Merge with Old History ---
if os.path.exists(FILE_PATH):
    print("Loading existing history...")
    try:
        with gzip.open(FILE_PATH, 'rb') as f:
            old_root = ET.fromstring(f.read())
            
        for channel in old_root.findall('channel'):
            cid = channel.get('id')
            name = channel.find('display-name').text or ""
            if keep_channel(name):
                valid_channel_ids.add(cid)
                if cid not in final_channels:
                    final_channels[cid] = channel

        for p in old_root.findall('programme'):
            cid = p.get('channel')
            start = p.get('start')
            if cid in valid_channel_ids:
                if (cid, start) not in merged_programmes:
                    merged_programmes[(cid, start)] = p
    except Exception as e:
        print(f"Warning: Could not load history ({e}). Starting fresh.")

# --- 6. Prune ---
print(f"Removing data before {CUTOFF_DATE}...")
final_prog_list = []

for key, prog in merged_programmes.items():
    start_dt = get_date_object(prog.get('start'))
    if start_dt and start_dt >= CUTOFF_DATE:
        final_prog_list.append(prog)

# --- 7. Save ---
print(f"Saving {FILE_PATH}...")
output_root = ET.Element("tv", new_root.attrib)

for cid in sorted(final_channels.keys()):
    output_root.append(final_channels[cid])

final_prog_list.sort(key=lambda x: x.get('start'))
for p in final_prog_list:
    output_root.append(p)

tree = ET.ElementTree(output_root)
with gzip.open(FILE_PATH, 'wb') as f:
    tree.write(f, encoding='utf-8', xml_declaration=True)

size_mb = os.path.getsize(FILE_PATH) / (1024 * 1024)
print(f"Success. New File Size: {size_mb:.2f} MB")
