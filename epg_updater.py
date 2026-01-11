import cloudscraper
import gzip
import os
import datetime
import xml.etree.ElementTree as ET
import re

# --- CONFIGURATION ---
RETENTION_DAYS = 30  # Keep 1 month of history
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
    """Filter Logic: Fox 501-507, TNT Sports, Sky Sports"""
    n = name.lower()
    
    # 1. Fox Sports AU (501-507 + News)
    if "fox" in n:
        if any(x in n for x in ['501', '502', '503', '504', '505', '506', '507', 'news', 'cricket', 'league', 'footy']):
            return True

    # 2. Sky Sports UK (Must have 'sky' AND 'sports')
    if "sky" in n and "sports" in n:
        return True

    # 3. TNT Sports UK (Must have 'tnt' AND 'sports')
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
    # Smart Detection (GZIP vs Plain)
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
    # Fix XML entities if broken
    xml_data = re.sub(r'&(?!(?:amp|lt|gt|apos|quot|#\d+|#x[0-9a-fA-F]+);)', '&amp;', xml_data)
    new_root = ET.fromstring(xml_data)

# Dictionary to hold merged programmes: key = (channel_id, start_time)
merged_programmes = {}
valid_channel_ids = set()
final_channels = {}

# A. Process NEW Channels
for channel in new_root.findall('channel'):
    cid = channel.get('id')
    name = channel.find('display-name').text or ""
    if keep_channel(name):
        valid_channel_ids.add(cid)
        final_channels[cid] = channel

# B. Process NEW Programmes
for p in new_root.findall('programme'):
    cid = p.get('channel')
    if cid in valid_channel_ids:
        # Convert Time
        start_ist = convert_to_ist(p.get('start'))
        stop_ist = convert_to_ist(p.get('stop'))
        p.set('start', start_ist)
        p.set('stop', stop_ist)
        
        # Save to merger dict
        merged_programmes[(cid, start_ist)] = p

# --- 5. Merge with Old History ---
if os.path.exists(FILE_PATH):
    print("Loading existing history to preserve data...")
    try:
        with gzip.open(FILE_PATH, 'rb') as f:
            old_root = ET.fromstring(f.read())
            
        # Extract OLD Channels (that match our filter)
        for channel in old_root.findall('channel'):
            cid = channel.get('id')
            name = channel.find('display-name').text or ""
            # Only keep history if it's one of our target channels
            if keep_channel(name):
                valid_channel_ids.add(cid)
                if cid not in final_channels:
                    final_channels[cid] = channel

        # Extract OLD Programmes
        for p in old_root.findall('programme'):
            cid = p.get('channel')
            start = p.get('start')
            
            # If it's a target channel AND we don't have this show in the new data yet
            if cid in valid_channel_ids:
                if (cid, start) not in merged_programmes:
                    merged_programmes[(cid, start)] = p
                    
    except Exception as e:
        print(f"Warning: Could not load old history ({e}). Starting fresh.")

# --- 6. Prune (Remove > 30 Days) ---
print(f"Pruning data older than {RETENTION_DAYS} days...")
cutoff = datetime.datetime.now() - datetime.timedelta(days=RETENTION_DAYS)
final_prog_list = []

for key, prog in merged_programmes.items():
    stop_dt = get_date_object(prog.get('stop'))
    
    # Keep if:
    # 1. We can't parse the date (safety)
    # 2. The show ends AFTER the cutoff date (it's recent)
    if not stop_dt or stop_dt > cutoff:
        final_prog_list.append(prog)

# --- 7. Save ---
print(f"Saving {FILE_PATH}...")
output_root = ET.Element("tv", new_root.attrib)

# Add Channels (Sorted by ID)
for cid in sorted(final_channels.keys()):
    output_root.append(final_channels[cid])

# Add Events (Sorted by Start Time)
final_prog_list.sort(key=lambda x: x.get('start'))
for p in final_prog_list:
    output_root.append(p)

tree = ET.ElementTree(output_root)
with gzip.open(FILE_PATH, 'wb') as f:
    tree.write(f, encoding='utf-8', xml_declaration=True)

size_mb = os.path.getsize(FILE_PATH) / (1024 * 1024)
print(f"Success. New File Size: {size_mb:.2f} MB")
