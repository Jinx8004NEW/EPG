import cloudscraper
import gzip
import os
import datetime
import xml.etree.ElementTree as ET
import re

# --- 1. Get Secrets ---
USERNAME = os.environ.get('CENTRA_USERNAME')
PASSWORD = os.environ.get('CENTRA_PASSWORD')

if not USERNAME or not PASSWORD:
    print("Error: CENTRA_USERNAME or CENTRA_PASSWORD not found in environment.")
    print("Make sure you added them in GitHub Settings > Secrets > Actions")
    exit(1)

URL = f"https://centra.ink/xmltv.php?username={USERNAME}&password={PASSWORD}"
FILE_PATH = "epg.xml.gz"

# --- 2. IST Converter (+0530) ---
def convert_to_ist(time_str):
    if not time_str: return ""
    try:
        dt_part = time_str.split()[0]
        dt = datetime.datetime.strptime(dt_part, '%Y%m%d%H%M%S')
        dt_ist = dt + datetime.timedelta(hours=5, minutes=30)
        return dt_ist.strftime('%Y%m%d%H%M%S') + " +0530"
    except Exception:
        return time_str

# --- 3. Download (Bypass Block) ---
print("Downloading EPG...")
scraper = cloudscraper.create_scraper()
try:
    response = scraper.get(URL)
    response.raise_for_status()
    xml_data = response.text
except Exception as e:
    print(f"Download Error: {e}")
    exit(1)

# --- 4. Parse & Clean ---
try:
    new_root = ET.fromstring(xml_data)
except ET.ParseError:
    # Clean invalid entities if parse fails
    xml_data = re.sub(r'&(?!(?:amp|lt|gt|apos|quot|#\d+|#x[0-9a-fA-F]+);)', '&amp;', xml_data)
    new_root = ET.fromstring(xml_data)

# --- 5. Convert Times ---
print("Converting to IST...")
for p in new_root.findall('programme'):
    p.set('start', convert_to_ist(p.get('start')))
    p.set('stop', convert_to_ist(p.get('stop')))

# --- 6. Merge History ---
if os.path.exists(FILE_PATH):
    print("Merging with existing data...")
    try:
        with gzip.open(FILE_PATH, 'rb') as f:
            old_root = ET.fromstring(f.read())
        
        # Merge Channels
        channels = {c.get('id'): c for c in old_root.findall('channel')}
        channels.update({c.get('id'): c for c in new_root.findall('channel')})
        
        # Merge Programmes
        programmes = {}
        for p in old_root.findall('programme'):
            programmes[(p.get('channel'), p.get('start'))] = p
        for p in new_root.findall('programme'):
            programmes[(p.get('channel'), p.get('start'))] = p
            
        output_root = ET.Element("tv", new_root.attrib)
        for c in channels.values(): output_root.append(c)
        for k in sorted(programmes.keys(), key=lambda x: x[1]): output_root.append(programmes[k])
        final_tree = ET.ElementTree(output_root)
    except:
        final_tree = ET.ElementTree(new_root)
else:
    print("No history found. Saving new data.")
    final_tree = ET.ElementTree(new_root)

# --- 7. Save ---
print(f"Saving {FILE_PATH}...")
with gzip.open(FILE_PATH, 'wb') as f:
    final_tree.write(f, encoding='utf-8', xml_declaration=True)
print("Success.")
