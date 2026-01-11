import cloudscraper
import gzip
import os
import datetime
import xml.etree.ElementTree as ET
import re

# --- CONFIGURATION ---
# ONLY keep events that start on or after this date
CUTOFF_DATE = datetime.datetime(2026, 1, 10)  # Jan 10, 2026
FILE_PATH = "epg.xml.gz"

# --- 1. Get Secrets ---
USERNAME = os.environ.get('CENTRA_USERNAME')
PASSWORD = os.environ.get('CENTRA_PASSWORD')

if not USERNAME or not PASSWORD:
    print("Error: CENTRA_USERNAME or CENTRA_PASSWORD not found.")
    exit(1)

URL = f"https://centra.ink/xmltv.php?username={USERNAME}&password={PASSWORD}&ext=.xml.gz"

# --- 2. Helper Functions ---
def convert_to_ist(time_str):
    """Converts a time string to IST (+0530)"""
    if not time_str: return ""
    try:
        dt_part = time_str.split()[0]
        dt = datetime.datetime.strptime(dt_part, '%Y%m%d%H%M%S')
        dt_ist = dt + datetime.timedelta(hours=5, minutes=30)
        return dt_ist.strftime('%Y%m%d%H%M%S') + " +0530"
    except Exception:
        return time_str

def keep_channel(name):
    """Filter Logic: Fox, Sky, TNT, and Kayo"""
    n = name.lower()
    
    # 1. Kayo Sports (New)
    if "kayo" in n:
        return True

    # 2. Fox Sports AU (501-507 + News)
    if "fox" in n:
        if any(x in n for x in ['501', '502', '503', '504', '505', '506', '507', 'news', 'cricket', 'league', 'footy']):
            return True

    # 3. Sky Sports UK
    if "sky" in n and "sports" in n:
        return True

    # 4. TNT Sports UK
    if "tnt" in n and "sports" in n:
        return True
        
    return False

def get_date_object(time_str):
    try:
        return datetime.datetime.strptime(time_str[:14], '%Y%m%d%H%M%S')
    except:
        return None

# --- 3. Download New Data ---
print("Downloading EPG...")
scraper = cloudscraper.create_scraper()
try:
    response = scraper.get(URL)
    response.raise_for_status()
    if response.content.startswith(b'\x1f\x8b'):
        xml_data = gzip.decompress(response.content).decode('utf-8')
    else:
        xml_data = response.text
except Exception as e:
    print(f"Download Error: {e}")
    exit(1)

# --- 4. Parse New Data ---
print("Parsing New Data...")
try:
    new_root = ET.fromstring(xml_data)
except ET.ParseError:
    xml_data = re.sub(r'&(?!(?:amp|lt|gt|apos|quot|#\d+|#x[0-9a-fA-F]+);)', '&amp;', xml_data)
    new_root = ET.fromstring(xml_data)

merged_programmes = {}
valid_channel_ids = set()
final_channels = {}

# Filter Channels (New)
for channel in new_root.findall('channel'):
    cid = channel.get('id')
    name = channel.find('display-name').text or ""
    if keep_channel(name):
        valid_channel_ids.add(cid)
        final_channels[cid] = channel

# Filter Programmes (New)
for p in new_root.findall('programme'):
    cid = p.get('channel')
    if cid in valid_channel_ids:
        start_ist = convert_to_ist(p.get('start'))
        stop_ist = convert_to_ist(p.get('stop'))
        p.set('start', start_ist)
        p.set('stop', stop_ist)
        merged_programmes[(cid, start_ist)] = p

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

# --- 6. Prune (Strict Date Filter: >= Jan 10, 2026) ---
print(f"Removing all data before {CUTOFF_DATE}...")
final_prog_list = []

for key, prog in merged_programmes.items():
    start_str = prog.get('start')
    start_dt = get_date_object(start_str)
    
    # Keep ONLY if start date is valid AND is on/after Jan 10 2026
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
