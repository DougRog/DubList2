from flask import Flask, render_template, request, jsonify, send_file
import os
import re
import json
import glob
import threading
import subprocess
from datetime import datetime
from ftplib import FTP
from difflib import SequenceMatcher
from werkzeug.utils import secure_filename
import time
import traceback
from collections import defaultdict
from io import BytesIO
import shutil

app = Flask(__name__)
app.secret_key = 'dub-automation-secret-key-2025'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

# Configuration
BASE_UPLOAD_DIR = '/mnt/IngestNew/Dubs/Uploads'
DOWNLOAD_DIR = '/mnt/IngestNew/Dubs/Downloaded'
WATCH_FOLDER = '/mnt/IngestNew/Dubs/Watch'
DATA_FOLDER = '/mnt/IngestNew/Dubs/Data'
REPORTS_FOLDER = '/mnt/IngestNew/Dubs/Reports'
MC_MEDIA_FOLDER = '/mnt/mc_media'

# Only match video files - exclude images, documents, etc.
VIDEO_EXTENSIONS = {
    '.mov', '.mp4', '.mxf', '.avi', '.wmv', '.mpg', '.mpeg',
    '.m4v', '.ts', '.mts', '.m2ts', '.dv', '.3gp', '.flv',
    '.mkv', '.webm', '.vob', '.ogv', '.rm', '.asf', '.f4v',
}

# FTP Configuration
FTP_HOST = '192.168.0.198'
FTP_PORT = 21
FTP_USER = 'creative'
FTP_PASS = 'MMxhZtqv6!'
FTP_SEARCH_DIRS = ['/MDSDublist-Commercial_Content_Upload', '/Lilly_Archive']

# Ensure all directories exist
for directory in [BASE_UPLOAD_DIR, DOWNLOAD_DIR, WATCH_FOLDER, DATA_FOLDER, REPORTS_FOLDER]:
    os.makedirs(directory, exist_ok=True)

ALLOWED_EXTENSIONS = {'dub', 'txt'}

# Source name conversion mapping
SOURCE_NAME_MAPPING = {
    'ERIE CREAT': 'Erie Creative',
    'ERIECREATI': 'Erie Creative',
    'EXTREME RE': 'Extreme Reach',
    'HIS TABERN': 'His Tabernacle',
    'ON THE SPO': 'On The Spot',
    'PUERTO RIC': 'Puerto Rico',
    'CARIBBEAN': 'Caribbean',
    'DEBBIE RIV': 'Debbie Rivera',
    'OMNICOM2': 'Omnicom',
    'ELMIRA CRE': 'Elmira Creative',
    'ELMIRACREA': 'Elmira Creative',
    'DOUG': 'Doug Productions'
}

# Global state
ftp_index = []
ftp_index_timestamp = None
ftp_index_directories = set()  # Track all directories found during indexing
ftp_index_progress = {'status': 'idle', 'current_dir': '', 'files_found': 0, 'progress': 0}
INDEX_REFRESH_HOURS = 1
INDEX_FILE = os.path.join(DATA_FOLDER, 'ftp_index.json')  # Written by ftp_indexer.py
download_queue = {}  # job_id -> job_info
queue_lock = threading.Lock()
ftp_download_semaphore = threading.Semaphore(1)  # Only one FTP download at a time
watchlist = {}  # house_id -> watchlist_item
watchlist_lock = threading.Lock()
watchlist_checking = False

# Load source mappings from file
def load_source_mappings():
    global SOURCE_NAME_MAPPING
    try:
        mappings_file = os.path.join(DATA_FOLDER, 'source_mappings.json')
        if os.path.exists(mappings_file):
            with open(mappings_file, 'r', encoding='utf-8') as f:
                loaded_mappings = json.load(f)
                SOURCE_NAME_MAPPING.update(loaded_mappings)
    except Exception as e:
        print(f"Error loading source mappings: {e}")

def save_source_mappings():
    try:
        mappings_file = os.path.join(DATA_FOLDER, 'source_mappings.json')
        with open(mappings_file, 'w', encoding='utf-8') as f:
            json.dump(SOURCE_NAME_MAPPING, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving source mappings: {e}")
        return False

load_source_mappings()

# Load and save watchlist
def load_watchlist():
    global watchlist
    try:
        watchlist_file = os.path.join(DATA_FOLDER, 'watchlist.json')
        if os.path.exists(watchlist_file):
            with open(watchlist_file, 'r', encoding='utf-8') as f:
                watchlist = json.load(f)
    except Exception as e:
        print(f"Error loading watchlist: {e}")

def save_watchlist():
    try:
        watchlist_file = os.path.join(DATA_FOLDER, 'watchlist.json')
        with open(watchlist_file, 'w', encoding='utf-8') as f:
            json.dump(watchlist, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving watchlist: {e}")
        return False

load_watchlist()

# App settings (persisted to disk)
app_settings = {
    'ftp_move_after_download': False,
    'ftp_move_destination': '/Downloaded'
}

def load_app_settings():
    global app_settings
    try:
        settings_file = os.path.join(DATA_FOLDER, 'app_settings.json')
        if os.path.exists(settings_file):
            with open(settings_file, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                app_settings.update(loaded)
    except Exception as e:
        print(f"Error loading app settings: {e}")

def save_app_settings():
    try:
        settings_file = os.path.join(DATA_FOLDER, 'app_settings.json')
        with open(settings_file, 'w', encoding='utf-8') as f:
            json.dump(app_settings, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving app settings: {e}")
        return False

load_app_settings()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def convert_source_name(source):
    """Convert abbreviated source names to full names"""
    if not source:
        return source
    
    if source in SOURCE_NAME_MAPPING:
        return SOURCE_NAME_MAPPING[source]
    
    for abbreviated, full_name in SOURCE_NAME_MAPPING.items():
        if source.startswith(abbreviated):
            return full_name
    
    return source.title()

def similarity_ratio(a, b):
    """Calculate similarity ratio between two strings"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def convert_seconds_to_timecode(seconds_str):
    """Convert seconds string to MM:SS:FF format (30fps)"""
    try:
        # Remove any non-numeric characters except decimal point
        seconds_str = ''.join(c for c in seconds_str if c.isdigit() or c == '.')
        if not seconds_str:
            return "00:00:00"

        total_seconds = float(seconds_str)
        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)
        frames = int((total_seconds % 1) * 30)

        return f"{minutes:02d}:{seconds:02d}:{frames:02d}"
    except Exception as e:
        print(f"Error converting duration '{seconds_str}': {e}")
        return seconds_str

def parse_dub_file(filepath):
    """Parse the .dub file and extract data according to specified column positions"""
    data = []

    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as file:
            for line_num, line in enumerate(file, 1):
                line = line.rstrip('\r\n')

                if not line.strip():
                    continue

                if len(line) > 259:
                    house_id = line[8:41].strip()
                    ad_id = line[190:241].strip()
                    duration_raw = line[240:249].strip()
                    source = line[248:259].strip()

                    # Remove stray character at the start of source
                    if source and source[0].isdigit():
                        source = source[1:]

                    source = convert_source_name(source)

                    # Convert duration from seconds to MM:SS:FF format
                    duration = convert_seconds_to_timecode(duration_raw)

                    # Skip if no house_id or ad_id
                    if not house_id and not ad_id:
                        continue

                    data.append({
                        'line_num': line_num,
                        'house_id': house_id,
                        'ad_id': ad_id,
                        'duration': duration,
                        'duration_raw': duration_raw,  # Keep original for reference
                        'source': source,
                        'status': 'pending',
                        'match_type': None,
                        'ftp_path': None,
                        'ftp_filename': None
                    })

    except Exception as e:
        print(f"Error parsing DUB file: {e}")
        return []

    return data

# ============================================
# FTP Index Functions (from Tool 1)
# ============================================

def build_ftp_index():
    """Build complete index of FTP files"""
    global ftp_index, ftp_index_timestamp, ftp_index_directories, ftp_index_progress

    print("\n" + "="*60)
    print("Building FTP index...")
    print("="*60)
    index = []
    directories = set()
    ftp_index_progress = {'status': 'indexing', 'current_dir': '', 'files_found': 0, 'progress': 0}
    
    def get_ftp_connection():
        ftp = FTP()
        ftp.connect(FTP_HOST, FTP_PORT, timeout=30)
        ftp.login(FTP_USER, FTP_PASS)
        return ftp

    # Shared FTP connection that can be updated
    ftp_connection = [None]  # Use list to allow updates in nested function

    def ensure_connection():
        """Ensure we have a valid FTP connection"""
        if ftp_connection[0] is None:
            ftp_connection[0] = get_ftp_connection()
        else:
            # Test connection
            try:
                ftp_connection[0].pwd()
            except:
                print(f"  Connection lost, reconnecting...")
                try:
                    ftp_connection[0].quit()
                except:
                    pass
                ftp_connection[0] = get_ftp_connection()
                time.sleep(0.5)
        return ftp_connection[0]

    def scan_directory(directory, depth=0, retry_count=0):
        if depth > 15:
            return

        directories.add(directory)
        ftp_index_progress['current_dir'] = directory
        ftp_index_progress['files_found'] = len(index)

        max_retries = 3

        try:
            # Ensure we have a valid connection
            ftp = ensure_connection()

            ftp.cwd(directory)
            items = []

            # Try MLSD first (modern, gives structured data), fall back to DIR
            try:
                mlsd_items = list(ftp.mlsd())
                for name, facts in mlsd_items:
                    if name in ['.', '..']:
                        continue
                    entry_type = facts.get('type', '').lower()
                    is_dir = entry_type in ('dir', 'cdir', 'pdir') or entry_type == ''
                    if entry_type == '':
                        try:
                            ftp.cwd(f"{directory}/{name}")
                            ftp.cwd(directory)
                            is_dir = True
                        except Exception:
                            is_dir = False
                    items.append({'name': name, 'is_dir': is_dir})
            except:
                # MLSD failed, try DIR
                try:
                    raw_lines = []
                    ftp.dir(raw_lines.append)
                    for item in raw_lines:
                        parts = item.split()
                        if len(parts) < 9:
                            continue
                        item_name = ' '.join(parts[8:])
                        is_dir_flag = item.startswith('d')
                        items.append({'name': item_name, 'is_dir': is_dir_flag})
                except Exception as e:
                    # Check if it's a connection error
                    if 'Broken pipe' in str(e) or 'Connection reset' in str(e):
                        if retry_count < max_retries:
                            print(f"  Connection error in {directory}, reconnecting... (attempt {retry_count + 1})")
                            ftp_connection[0] = None  # Force reconnect
                            time.sleep(1)
                            return scan_directory(directory, depth, retry_count + 1)
                    print(f"  Cannot list {directory}: {e}")
                    return

            for entry in items:
                item_name = entry['name']
                is_dir = entry['is_dir']

                if is_dir:
                    if any(char in item_name for char in ['?', '*', '"', '<', '>', '|']):
                        continue

                    subdir = f"{directory}/{item_name}".replace('//', '/')

                    try:
                        scan_directory(subdir, depth + 1)
                    except Exception as e:
                        error_str = str(e)
                        if 'Broken pipe' in error_str or 'Connection reset' in error_str:
                            print(f"  Connection error in subdirectory, will retry...")
                            ftp_connection[0] = None  # Force reconnect on next call
                            time.sleep(0.5)
                        elif depth < 3:
                            print(f"  Error scanning subdir {subdir}: {e}")
                else:
                    full_path = f"{directory}/{item_name}".replace('//', '/')
                    base_name = os.path.splitext(item_name)[0]
                    index.append({
                        'filename': item_name,
                        'basename': base_name,
                        'path': full_path
                    })

        except Exception as e:
            error_str = str(e)
            if 'Broken pipe' in error_str or 'Connection reset' in error_str:
                if retry_count < max_retries:
                    print(f"  Connection error in {directory}, reconnecting... (attempt {retry_count + 1})")
                    ftp_connection[0] = None  # Force reconnect
                    time.sleep(1)
                    return scan_directory(directory, depth, retry_count + 1)
            if depth < 3:
                print(f"  Error in {directory}: {e}")
    
    try:
        # Initialize connection
        ftp_connection[0] = get_ftp_connection()

        for search_dir in FTP_SEARCH_DIRS:
            print(f"\n→ Starting scan of: {search_dir}")
            try:
                ftp = ensure_connection()
                ftp.cwd(search_dir)
                print(f"  ✓ Successfully accessed {search_dir}")

                scan_directory(search_dir)

            except Exception as e:
                print(f"  ✗ Error with {search_dir}: {e}")
                ftp_connection[0] = None  # Force reconnect for next directory

        # Clean up
        try:
            if ftp_connection[0]:
                ftp_connection[0].quit()
        except:
            pass
        
        ftp_index = index
        ftp_index_directories = directories
        ftp_index_timestamp = datetime.now()
        ftp_index_progress = {'status': 'complete', 'current_dir': '', 'files_found': len(index), 'progress': 100}

        print(f"\n" + "="*60)
        print(f"INDEX BUILD COMPLETE: {len(ftp_index)} files indexed from {len(directories)} directories")
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"Critical error building FTP index: {e}")
        traceback.print_exc()
        if not ftp_index:
            ftp_index = []
        ftp_index_timestamp = datetime.now()
        ftp_index_progress = {'status': 'error', 'current_dir': '', 'files_found': len(ftp_index), 'progress': 0, 'error': str(e)}

def load_ftp_index_from_disk():
    """Load the FTP index from the JSON file written by ftp_indexer.py"""
    global ftp_index, ftp_index_timestamp, ftp_index_directories, ftp_index_progress

    if not os.path.exists(INDEX_FILE):
        return False

    try:
        with open(INDEX_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)

        ftp_index = data.get('index', [])
        ftp_index_directories = set(data.get('directories', []))
        ftp_index_timestamp = datetime.fromisoformat(data['timestamp'])
        ftp_index_progress = {
            'status': 'complete',
            'current_dir': '',
            'files_found': data.get('file_count', len(ftp_index)),
            'progress': 100
        }

        age = (datetime.now() - ftp_index_timestamp).total_seconds()
        print(f"Loaded FTP index from disk: {len(ftp_index)} files ({age:.0f}s old)")
        return True

    except Exception as e:
        print(f"Error loading FTP index from disk: {e}")
        return False


def get_ftp_index():
    """Get FTP index - loads from disk file first, falls back to live FTP scan"""
    global ftp_index_timestamp

    if not ftp_index or not ftp_index_timestamp:
        # Try loading from disk first (written by ftp_indexer.py)
        if not load_ftp_index_from_disk():
            print("No index file found on disk, building from FTP...")
            build_ftp_index()
    else:
        # Check if the disk file is newer than what we have in memory
        if os.path.exists(INDEX_FILE):
            try:
                file_mtime = datetime.fromtimestamp(os.path.getmtime(INDEX_FILE))
                if file_mtime > ftp_index_timestamp:
                    load_ftp_index_from_disk()
            except Exception:
                pass

        # If the index is very old AND no disk file, fall back to live scan
        if ftp_index_timestamp:
            age = datetime.now() - ftp_index_timestamp
            if age.total_seconds() > (INDEX_REFRESH_HOURS * 3600):
                if not load_ftp_index_from_disk():
                    print("FTP index is stale and no disk file, refreshing from FTP...")
                    build_ftp_index()

    return ftp_index

def search_ftp_index(search_term, source_filter=None):
    """Search FTP index with exact, partial, and fuzzy matching

    Args:
        search_term: The term to search for
        source_filter: Optional source directory to filter results (e.g., 'Erie Creative')
    """
    if not search_term:
        return {'exact': [], 'partial': [], 'fuzzy': [], 'searched_everywhere': False, 'searched_archive': False}

    index = get_ftp_index()
    search_clean = search_term.strip().lower()

    # First, try searching with source filter if provided
    def search_in_index(index_subset):
        exact_matches = []
        partial_matches = []
        fuzzy_matches = []

        # Only consider video files (belt-and-suspenders in case index has non-video entries)
        index_subset = [
            e for e in index_subset
            if os.path.splitext(e['filename'])[1].lower() in VIDEO_EXTENSIONS
        ]

        for entry in index_subset:
            basename_lower = entry['basename'].lower()

            # Exact match
            if basename_lower == search_clean:
                exact_matches.append(entry)
            # Partial match (contains)
            elif search_clean in basename_lower or basename_lower in search_clean:
                partial_matches.append(entry)
            else:
                # Fuzzy match — skip trivially short filenames to avoid spurious matches
                if len(basename_lower) <= 3:
                    continue
                similarity = similarity_ratio(search_clean, basename_lower)
                if similarity >= 0.75:
                    fuzzy_matches.append({
                        'entry': entry,
                        'similarity': similarity
                    })

        fuzzy_matches.sort(key=lambda x: x['similarity'], reverse=True)
        fuzzy_matches = [item['entry'] for item in fuzzy_matches[:10]]

        return exact_matches, partial_matches, fuzzy_matches

    searched_everywhere = False
    searched_archive = False

    # If source filter provided, search in source directory first
    if source_filter:
        # Filter index by source directory
        source_clean = source_filter.lower().replace(' ', '').replace('_', '')
        filtered_index = [
            entry for entry in index
            if source_clean in entry['path'].lower().replace(' ', '').replace('_', '')
        ]

        exact_matches, partial_matches, fuzzy_matches = search_in_index(filtered_index)

        # If no exact match found in source directory, also check Lilly_Archive.
        # Files may have been moved there from the original source folder.
        if not exact_matches:
            archive_index = [
                entry for entry in index
                if '/lilly_archive' in entry['path'].lower()
            ]

            if archive_index:
                arch_exact, arch_partial, arch_fuzzy = search_in_index(archive_index)
                if arch_exact:
                    # Prefer exact archive match over any source partial/fuzzy
                    exact_matches = arch_exact
                    searched_archive = True
                elif not partial_matches and not fuzzy_matches:
                    # Nothing found in source at all; use archive partial/fuzzy
                    if arch_partial or arch_fuzzy:
                        partial_matches = arch_partial
                        fuzzy_matches = arch_fuzzy
                        searched_archive = True

        # Still no matches anywhere? Search all indexed directories
        if not exact_matches and not partial_matches and not fuzzy_matches:
            searched_everywhere = True
            exact_matches, partial_matches, fuzzy_matches = search_in_index(index)
    else:
        # No source filter, search everywhere
        exact_matches, partial_matches, fuzzy_matches = search_in_index(index)

    return {
        'exact': exact_matches,
        'partial': partial_matches,
        'fuzzy': fuzzy_matches,
        'searched_everywhere': searched_everywhere,
        'searched_archive': searched_archive
    }

# ============================================
# Queue Management
# ============================================

def add_to_queue(spot_data, session_id):
    """Add approved spot to download queue"""
    job_id = f"{session_id}_{spot_data['line_num']}"
    ftp_path = spot_data.get('ftp_path')

    with queue_lock:
        # Skip if this exact job is already active or completed (prevents double-clicks / re-approvals)
        existing = download_queue.get(job_id)
        if existing and existing['status'] != 'failed':
            return job_id

        # Skip if the same FTP file is already actively downloading in this session
        if ftp_path:
            for j in download_queue.values():
                if (j['session_id'] == session_id
                        and j.get('ftp_path') == ftp_path
                        and j['status'] in ('queued', 'downloading', 'validating')):
                    return j['job_id']

        download_queue[job_id] = {
            'job_id': job_id,
            'session_id': session_id,
            'line_num': spot_data['line_num'],
            'house_id': spot_data['house_id'],
            'ad_id': spot_data['ad_id'],
            'expected_duration': spot_data['duration'],
            'source': spot_data['source'],
            'ftp_path': ftp_path,
            'ftp_filename': spot_data.get('ftp_filename'),
            'status': 'queued',
            'progress': 0,
            'message': 'Waiting in queue',
            'local_path': None,
            'actual_duration': None,
            'duration_match': None,
            'final_path': None,
            'added_time': datetime.now().isoformat()
        }

    # Start download in background thread
    threading.Thread(target=process_download_job, args=(job_id,), daemon=True).start()

    return job_id

def process_download_job(job_id):
    """Process a single download job"""
    try:
        with queue_lock:
            job = download_queue.get(job_id)
            if not job:
                return
            job['message'] = 'Waiting for available FTP connection...'

        # Serialize FTP downloads — only one connection at a time
        ftp_download_semaphore.acquire()
        try:
            with queue_lock:
                job['status'] = 'downloading'
                job['progress'] = 10
                job['message'] = 'Connecting to FTP...'

            ftp_path = job['ftp_path']
            filename = os.path.basename(ftp_path)
            temp_path = os.path.join(DOWNLOAD_DIR, f"temp_{job_id}_{filename}")

            with queue_lock:
                job['progress'] = 30
                job['message'] = 'Downloading file...'

            ftp = FTP()
            ftp.connect(FTP_HOST, FTP_PORT, timeout=60)
            ftp.login(FTP_USER, FTP_PASS)

            try:
                with open(temp_path, 'wb') as f:
                    ftp.retrbinary(f'RETR {ftp_path}', f.write)
            except Exception as retr_err:
                # 550 = file not found — it was in the index but got moved/deleted
                if '550' in str(retr_err):
                    print(f"File not found at indexed path, re-searching: {ftp_path}")
                    with queue_lock:
                        job['progress'] = 15
                        job['message'] = 'File moved/deleted, re-searching index...'

                    # Clean up partial temp file
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

                    # Re-search using ad_id then house_id
                    search_terms = [job['ad_id'], job['house_id']]
                    search_terms = [t for t in search_terms if t and t.strip()]
                    new_path = None

                    for term in search_terms:
                        results = search_ftp_index(term, job.get('source'))
                        matches = results.get('exact', []) or results.get('partial', [])
                        for m in matches:
                            if m['path'] != ftp_path:  # Skip the stale path
                                new_path = m['path']
                                break
                        if new_path:
                            break

                    if not new_path:
                        raise Exception(f'File no longer on FTP server: {filename}')

                    # Retry download with the new path
                    print(f"Found alternate path: {new_path}")
                    filename = os.path.basename(new_path)
                    temp_path = os.path.join(DOWNLOAD_DIR, f"temp_{job_id}_{filename}")

                    with queue_lock:
                        job['ftp_path'] = new_path
                        job['ftp_filename'] = filename
                        job['progress'] = 30
                        job['message'] = f'Downloading from alternate path...'

                    # Reconnect in case the previous error closed the session
                    try:
                        ftp.quit()
                    except Exception:
                        pass
                    ftp = FTP()
                    ftp.connect(FTP_HOST, FTP_PORT, timeout=60)
                    ftp.login(FTP_USER, FTP_PASS)

                    with open(temp_path, 'wb') as f:
                        ftp.retrbinary(f'RETR {new_path}', f.write)
                else:
                    raise

            # Keep track of the downloaded FTP path for the move step
            downloaded_ftp_path = job['ftp_path']

            with queue_lock:
                job['status'] = 'validating'
                job['progress'] = 60
                job['message'] = 'Validating duration...'
                job['local_path'] = temp_path

            # Validate duration
            actual_duration = get_video_duration(temp_path)

            with queue_lock:
                job['actual_duration'] = actual_duration
                job['duration_match'] = check_duration_match(job['expected_duration'], actual_duration)
                job['progress'] = 80
                job['message'] = 'Renaming and transferring...'

            # Rename using House ID
            house_id = job['house_id']
            extension = os.path.splitext(filename)[1]
            new_filename = f"{house_id}{extension}"
            final_path = os.path.join(WATCH_FOLDER, new_filename)

            # Move to watch folder
            shutil.move(temp_path, final_path)

            # Move file on FTP server if enabled
            if app_settings.get('ftp_move_after_download'):
                dest_dir = app_settings.get('ftp_move_destination', '/Downloaded').rstrip('/')
                ftp_filename = os.path.basename(downloaded_ftp_path)
                dest_path = f"{dest_dir}/{ftp_filename}"

                with queue_lock:
                    job['progress'] = 90
                    job['message'] = f'Moving file on FTP to {dest_dir}/...'

                try:
                    # Reconnect for the move (download may have closed the session)
                    try:
                        ftp.pwd()
                    except Exception:
                        ftp = FTP()
                        ftp.connect(FTP_HOST, FTP_PORT, timeout=60)
                        ftp.login(FTP_USER, FTP_PASS)

                    # Ensure destination directory exists
                    try:
                        ftp.cwd(dest_dir)
                    except Exception:
                        ftp.mkd(dest_dir)

                    ftp.rename(downloaded_ftp_path, dest_path)
                    print(f"FTP move: {downloaded_ftp_path} -> {dest_path}")
                except Exception as move_err:
                    # Log but don't fail the job — the download itself succeeded
                    print(f"Warning: FTP move failed for {ftp_filename}: {move_err}")

            try:
                ftp.quit()
            except Exception:
                pass

            with queue_lock:
                job['status'] = 'completed'
                job['progress'] = 100
                job['final_path'] = final_path

                if job['duration_match']:
                    job['message'] = f'✓ Complete - Duration matches ({actual_duration})'
                else:
                    job['message'] = f'⚠ Complete - Duration mismatch (Expected: {job["expected_duration"]}, Actual: {actual_duration})'

        finally:
            ftp_download_semaphore.release()

    except Exception as e:
        with queue_lock:
            job = download_queue.get(job_id)
            if job:
                job['status'] = 'failed'
                job['progress'] = 0
                job['message'] = f'Error: {str(e)}'
        print(f"Error processing job {job_id}: {e}")
        traceback.print_exc()

def get_video_duration(filepath):
    """Get video duration using ffprobe"""
    try:
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            filepath
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            duration_seconds = float(result.stdout.strip())
            
            # Convert to MM:SS:FF format (assuming 30fps)
            minutes = int(duration_seconds // 60)
            seconds = int(duration_seconds % 60)
            frames = int((duration_seconds % 1) * 30)
            
            return f"{minutes:02d}:{seconds:02d}:{frames:02d}"
        else:
            return "Unknown"
    
    except Exception as e:
        print(f"Error getting video duration: {e}")
        return "Unknown"

def check_duration_match(expected, actual):
    """Check if expected and actual durations match (within 1 second tolerance)"""
    if not expected or not actual or actual == "Unknown":
        return None
    
    try:
        # Parse expected format (could be MM:SS:FF or :SS)
        if ':' in expected:
            parts = expected.split(':')
            if len(parts) == 3:
                exp_seconds = int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 30.0
            elif len(parts) == 2:
                exp_seconds = int(parts[0]) * 60 + int(parts[1])
            else:
                return None
        else:
            exp_seconds = float(expected)
        
        # Parse actual format
        if ':' in actual:
            parts = actual.split(':')
            if len(parts) == 3:
                act_seconds = int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 30.0
            elif len(parts) == 2:
                act_seconds = int(parts[0]) * 60 + int(parts[1])
            else:
                return None
        else:
            act_seconds = float(actual)
        
        # Check if within 1 second tolerance
        return abs(exp_seconds - act_seconds) <= 1.0
    
    except Exception as e:
        print(f"Error comparing durations: {e}")
        return None

# ============================================
# Report Generation
# ============================================

def generate_report(session_id, dub_filename, spots_data):
    """Generate processing report"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_filename = f"report_{session_id}_{timestamp}.html"
        report_path = os.path.join(REPORTS_FOLDER, report_filename)
        
        # Get queue jobs for this session
        with queue_lock:
            session_jobs = {jid: job for jid, job in download_queue.items() if job['session_id'] == session_id}
        
        # Categorize results
        processed = []
        missing = []
        mismatched = []
        
        for spot in spots_data:
            job_id = f"{session_id}_{spot['line_num']}"
            job = session_jobs.get(job_id)
            
            if job:
                if job['status'] == 'completed':
                    if job['duration_match'] is False:
                        mismatched.append({**spot, **job})
                    else:
                        processed.append({**spot, **job})
                elif job['status'] == 'failed':
                    missing.append({**spot, **job})
            elif spot['status'] == 'no_match':
                missing.append(spot)
        
        # Generate HTML report
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>DUB Processing Report - {dub_filename}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
        .header {{ background: white; padding: 30px; border-radius: 8px; margin-bottom: 30px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; margin: 0 0 10px 0; }}
        .meta {{ color: #666; font-size: 14px; }}
        .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
        .summary-card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); text-align: center; }}
        .summary-card .number {{ font-size: 36px; font-weight: bold; margin: 10px 0; }}
        .summary-card .label {{ color: #666; font-size: 14px; }}
        .summary-card.success .number {{ color: #10b981; }}
        .summary-card.missing .number {{ color: #ef4444; }}
        .summary-card.mismatch .number {{ color: #f59e0b; }}
        .summary-card.total .number {{ color: #3b82f6; }}
        .section {{ background: white; padding: 30px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .section h2 {{ margin-top: 0; color: #333; border-bottom: 2px solid #e5e7eb; padding-bottom: 10px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th {{ background: #f9fafb; padding: 12px; text-align: left; font-weight: 600; border-bottom: 2px solid #e5e7eb; }}
        td {{ padding: 12px; border-bottom: 1px solid #e5e7eb; }}
        tr:hover {{ background: #f9fafb; }}
        .status {{ padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
        .status.success {{ background: #d1fae5; color: #065f46; }}
        .status.error {{ background: #fee2e2; color: #991b1b; }}
        .status.warning {{ background: #fef3c7; color: #92400e; }}
        @media print {{ body {{ margin: 20px; }} }}
    </style>
</head>
<body>
    <div class="header">
        <h1>DUB Processing Report</h1>
        <div class="meta">
            <div><strong>DUB File:</strong> {dub_filename}</div>
            <div><strong>Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
            <div><strong>Session ID:</strong> {session_id}</div>
        </div>
    </div>
    
    <div class="summary">
        <div class="summary-card total">
            <div class="label">Total Spots</div>
            <div class="number">{len(spots_data)}</div>
        </div>
        <div class="summary-card success">
            <div class="label">Successfully Processed</div>
            <div class="number">{len(processed)}</div>
        </div>
        <div class="summary-card missing">
            <div class="label">Missing Files</div>
            <div class="number">{len(missing)}</div>
        </div>
        <div class="summary-card mismatch">
            <div class="label">Duration Mismatches</div>
            <div class="number">{len(mismatched)}</div>
        </div>
    </div>
"""
        
        # Successfully Processed Section
        if processed:
            html += """
    <div class="section">
        <h2>✓ Successfully Processed ({} files)</h2>
        <table>
            <thead>
                <tr>
                    <th>Line</th>
                    <th>House ID</th>
                    <th>Ad ID</th>
                    <th>Expected Duration</th>
                    <th>Actual Duration</th>
                    <th>Source</th>
                    <th>Final Path</th>
                </tr>
            </thead>
            <tbody>
""".format(len(processed))
            
            for item in processed:
                html += f"""
                <tr>
                    <td>{item['line_num']}</td>
                    <td><strong>{item['house_id']}</strong></td>
                    <td>{item['ad_id']}</td>
                    <td>{item['expected_duration']}</td>
                    <td>{item.get('actual_duration', 'N/A')}</td>
                    <td>{item['source']}</td>
                    <td style="font-size: 11px;">{item.get('final_path', 'N/A')}</td>
                </tr>
"""
            html += """
            </tbody>
        </table>
    </div>
"""
        
        # Missing Files Section
        if missing:
            html += """
    <div class="section">
        <h2>⚠ Missing Files ({} files)</h2>
        <p style="color: #6b7280; margin-bottom: 15px;">
            These items have been automatically added to the watchlist and will be checked periodically for matches.
        </p>
        <table>
            <thead>
                <tr>
                    <th>Line</th>
                    <th>House ID</th>
                    <th>Ad ID</th>
                    <th>Duration</th>
                    <th>Source</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
""".format(len(missing))

            for item in missing:
                status_msg = item.get('message', 'No FTP match found - Added to watchlist')
                html += f"""
                <tr>
                    <td>{item['line_num']}</td>
                    <td><strong>{item['house_id']}</strong></td>
                    <td>{item['ad_id']}</td>
                    <td>{item['duration']}</td>
                    <td>{item['source']}</td>
                    <td><span class="status error">{status_msg}</span></td>
                </tr>
"""
            html += """
            </tbody>
        </table>
    </div>
"""
        
        # Duration Mismatches Section
        if mismatched:
            html += """
    <div class="section">
        <h2>⚠ Duration Mismatches ({} files)</h2>
        <table>
            <thead>
                <tr>
                    <th>Line</th>
                    <th>House ID</th>
                    <th>Ad ID</th>
                    <th>Expected Duration</th>
                    <th>Actual Duration</th>
                    <th>Difference</th>
                    <th>Source</th>
                </tr>
            </thead>
            <tbody>
""".format(len(mismatched))
            
            for item in mismatched:
                html += f"""
                <tr>
                    <td>{item['line_num']}</td>
                    <td><strong>{item['house_id']}</strong></td>
                    <td>{item['ad_id']}</td>
                    <td>{item['expected_duration']}</td>
                    <td>{item.get('actual_duration', 'N/A')}</td>
                    <td><span class="status warning">Mismatch</span></td>
                    <td>{item['source']}</td>
                </tr>
"""
            html += """
            </tbody>
        </table>
    </div>
"""
        
        html += """
</body>
</html>
"""
        
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        return report_filename
    
    except Exception as e:
        print(f"Error generating report: {e}")
        traceback.print_exc()
        return None

# ============================================
# Flask Routes
# ============================================

@app.route('/')
def index():
    return render_template('dub_automation.html')

@app.route('/api/upload', methods=['POST'])
def upload_dub_file():
    """Upload and parse DUB file"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Please upload .dub or .txt file'}), 400
    
    try:
        filename = secure_filename(file.filename)
        session_id = datetime.now().strftime('%Y%m%d_%H%M%S')
        filepath = os.path.join(BASE_UPLOAD_DIR, f"{session_id}_{filename}")
        file.save(filepath)
        
        # Parse DUB file
        spots_data = parse_dub_file(filepath)
        
        if not spots_data:
            return jsonify({'error': 'No valid data found in DUB file'}), 400
        
        # Save session data
        session_data = {
            'session_id': session_id,
            'filename': filename,
            'upload_time': datetime.now().isoformat(),
            'spots': spots_data
        }
        
        session_file = os.path.join(DATA_FOLDER, f"session_{session_id}.json")
        with open(session_file, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, indent=2)
        
        return jsonify({
            'success': True,
            'session_id': session_id,
            'filename': filename,
            'spot_count': len(spots_data),
            'spots': spots_data
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/search_all', methods=['POST'])
def search_all_spots():
    """Search FTP for all spots in a session"""
    data = request.json
    session_id = data.get('session_id')
    spots = data.get('spots', [])

    if not session_id or not spots:
        return jsonify({'error': 'Missing session_id or spots'}), 400

    results = []

    for spot in spots:
        # Search by Ad ID first, then House ID
        search_terms = [spot['ad_id'], spot['house_id']]
        search_terms = [term for term in search_terms if term and term.strip()]

        best_match = None
        source_filter = spot.get('source')  # Use source from dub list for filtering

        for search_term in search_terms:
            ftp_results = search_ftp_index(search_term, source_filter)

            if ftp_results['exact']:
                best_match = {
                    'type': 'exact',
                    'matches': [{'path': f['path'], 'filename': f['filename']} for f in ftp_results['exact']],
                    'search_term': search_term,
                    'searched_everywhere': ftp_results.get('searched_everywhere', False),
                    'searched_archive': ftp_results.get('searched_archive', False)
                }
                break
            elif ftp_results['partial'] and not best_match:
                best_match = {
                    'type': 'partial',
                    'matches': [{'path': f['path'], 'filename': f['filename']} for f in ftp_results['partial']],
                    'search_term': search_term,
                    'searched_everywhere': ftp_results.get('searched_everywhere', False),
                    'searched_archive': ftp_results.get('searched_archive', False)
                }
            elif ftp_results['fuzzy'] and not best_match:
                best_match = {
                    'type': 'fuzzy',
                    'matches': [{'path': f['path'], 'filename': f['filename']} for f in ftp_results['fuzzy']],
                    'search_term': search_term,
                    'searched_everywhere': ftp_results.get('searched_everywhere', False),
                    'searched_archive': ftp_results.get('searched_archive', False)
                }

        # Auto-add to watchlist if no match found
        if not best_match:
            with watchlist_lock:
                if spot['house_id'] not in watchlist:
                    watchlist[spot['house_id']] = {
                        'house_id': spot['house_id'],
                        'ad_id': spot['ad_id'],
                        'duration': spot['duration'],
                        'source': spot.get('source'),
                        'added_date': datetime.now().isoformat(),
                        'exclude': False,
                        'last_checked': None,
                        'match_found': False,
                        'auto_added': True
                    }
                    save_watchlist()

        # Check if a file with this House ID already exists in the media library
        mc_exists = bool(glob.glob(os.path.join(MC_MEDIA_FOLDER, f"{spot['house_id']}.*")))

        results.append({
            'line_num': spot['line_num'],
            'house_id': spot['house_id'],
            'ad_id': spot['ad_id'],
            'match': best_match,
            'mc_exists': mc_exists
        })

    return jsonify({'success': True, 'results': results})

@app.route('/api/approve', methods=['POST'])
def approve_spot():
    """Approve a spot match and add to download queue"""
    data = request.json
    session_id = data.get('session_id')
    spot_data = data.get('spot')
    
    if not session_id or not spot_data:
        return jsonify({'error': 'Missing required data'}), 400
    
    job_id = add_to_queue(spot_data, session_id)
    
    return jsonify({'success': True, 'job_id': job_id})

@app.route('/api/queue_status')
def queue_status():
    """Get current queue status"""
    session_id = request.args.get('session_id')
    
    with queue_lock:
        if session_id:
            jobs = {jid: job for jid, job in download_queue.items() if job['session_id'] == session_id}
        else:
            jobs = download_queue.copy()
    
    return jsonify({'jobs': list(jobs.values())})

@app.route('/api/generate_report', methods=['POST'])
def generate_report_endpoint():
    """Generate processing report"""
    data = request.json
    session_id = data.get('session_id')
    
    if not session_id:
        return jsonify({'error': 'Missing session_id'}), 400
    
    try:
        # Load session data
        session_file = os.path.join(DATA_FOLDER, f"session_{session_id}.json")
        with open(session_file, 'r', encoding='utf-8') as f:
            session_data = json.load(f)
        
        report_filename = generate_report(
            session_id,
            session_data['filename'],
            session_data['spots']
        )
        
        if report_filename:
            return jsonify({
                'success': True,
                'report_filename': report_filename,
                'download_url': f'/api/download_report/{report_filename}'
            })
        else:
            return jsonify({'error': 'Failed to generate report'}), 500
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/download_report/<filename>')
def download_report(filename):
    """Download a generated report"""
    report_path = os.path.join(REPORTS_FOLDER, filename)
    if os.path.exists(report_path):
        return send_file(report_path, as_attachment=True)
    else:
        return jsonify({'error': 'Report not found'}), 404

@app.route('/api/refresh_ftp_index', methods=['POST'])
def refresh_ftp_index():
    """Refresh FTP index - always triggers a live FTP scan so moved files (e.g. to Lilly_Archive) are picked up"""
    try:
        # Load any existing disk file immediately so searches continue working during the scan
        if os.path.exists(INDEX_FILE):
            load_ftp_index_from_disk()
        # Always start a fresh background scan regardless of whether a disk file exists
        threading.Thread(target=build_ftp_index, daemon=True).start()
        return jsonify({
            'success': True,
            'message': 'FTP scan started in background',
            'source': 'ftp'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/source_mappings', methods=['GET'])
def get_source_mappings():
    """Get current source mappings"""
    return jsonify({'mappings': SOURCE_NAME_MAPPING})

@app.route('/api/source_mappings', methods=['POST'])
def update_source_mapping_route():
    """Add or update source mapping"""
    data = request.json
    abbreviated = data.get('abbreviated', '').strip().upper()
    full_name = data.get('full_name', '').strip()
    
    if abbreviated and full_name:
        SOURCE_NAME_MAPPING[abbreviated] = full_name
        save_source_mappings()
        return jsonify({'success': True, 'message': f'Mapping saved: {abbreviated} → {full_name}'})
    else:
        return jsonify({'error': 'Both abbreviated and full name are required'}), 400

@app.route('/api/source_mappings/<abbreviated>', methods=['DELETE'])
def delete_source_mapping_route(abbreviated):
    """Delete a source mapping"""
    abbreviated = abbreviated.upper().strip()
    if abbreviated in SOURCE_NAME_MAPPING:
        del SOURCE_NAME_MAPPING[abbreviated]
        save_source_mappings()
        return jsonify({'success': True, 'message': f'Mapping deleted: {abbreviated}'})
    else:
        return jsonify({'error': 'Mapping not found'}), 404

@app.route('/api/settings', methods=['GET'])
def get_app_settings():
    """Get current app settings"""
    return jsonify({'settings': app_settings})

@app.route('/api/settings', methods=['POST'])
def update_app_settings():
    """Update app settings"""
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    for key in data:
        if key in app_settings:
            app_settings[key] = data[key]

    save_app_settings()
    return jsonify({'success': True, 'settings': app_settings})

@app.route('/api/ftp_progress')
def get_ftp_progress():
    """Get FTP indexing progress"""
    return jsonify(ftp_index_progress)

@app.route('/api/ftp_directories')
def get_ftp_directories():
    """Get list of all indexed directories"""
    return jsonify({'directories': sorted(list(ftp_index_directories))})

@app.route('/api/watchlist', methods=['GET'])
def get_watchlist():
    """Get all watchlist items"""
    with watchlist_lock:
        return jsonify({'watchlist': list(watchlist.values())})

@app.route('/api/watchlist', methods=['POST'])
def add_to_watchlist():
    """Add item to watchlist"""
    data = request.json
    house_id = data.get('house_id')
    ad_id = data.get('ad_id')
    duration = data.get('duration')
    source = data.get('source')
    exclude = data.get('exclude', False)

    if not house_id:
        return jsonify({'error': 'house_id is required'}), 400

    with watchlist_lock:
        watchlist[house_id] = {
            'house_id': house_id,
            'ad_id': ad_id,
            'duration': duration,
            'source': source,
            'added_date': datetime.now().isoformat(),
            'exclude': exclude,
            'last_checked': None,
            'match_found': False
        }
        save_watchlist()

    return jsonify({'success': True, 'message': f'Added {house_id} to watchlist'})

@app.route('/api/watchlist/clear', methods=['DELETE'])
def clear_watchlist():
    """Remove all items from watchlist"""
    with watchlist_lock:
        count = len(watchlist)
        watchlist.clear()
        save_watchlist()
    return jsonify({'success': True, 'message': f'Removed all {count} item(s) from watchlist'})

@app.route('/api/watchlist/<house_id>', methods=['DELETE'])
def remove_from_watchlist(house_id):
    """Remove item from watchlist"""
    with watchlist_lock:
        if house_id in watchlist:
            del watchlist[house_id]
            save_watchlist()
            return jsonify({'success': True, 'message': f'Removed {house_id} from watchlist'})
        else:
            return jsonify({'error': 'Item not found in watchlist'}), 404

@app.route('/api/watchlist/<house_id>/exclude', methods=['POST'])
def toggle_watchlist_exclude(house_id):
    """Toggle exclude flag for watchlist item"""
    data = request.json
    exclude = data.get('exclude', True)

    with watchlist_lock:
        if house_id in watchlist:
            watchlist[house_id]['exclude'] = exclude
            save_watchlist()
            return jsonify({'success': True, 'excluded': exclude})
        else:
            return jsonify({'error': 'Item not found in watchlist'}), 404

@app.route('/api/watchlist/check', methods=['POST'])
def check_watchlist():
    """Check watchlist for matches"""
    global watchlist_checking

    if watchlist_checking:
        return jsonify({'error': 'Watchlist check already in progress'}), 400

    def check_watchlist_task():
        global watchlist_checking
        watchlist_checking = True

        try:
            with watchlist_lock:
                items_to_check = [item for item in watchlist.values() if not item.get('exclude', False)]

            matches_found = []

            for item in items_to_check:
                search_terms = [item['ad_id'], item['house_id']]
                search_terms = [term for term in search_terms if term and term.strip()]

                for search_term in search_terms:
                    ftp_results = search_ftp_index(search_term, item.get('source'))

                    if ftp_results['exact'] or ftp_results['partial']:
                        with watchlist_lock:
                            watchlist[item['house_id']]['match_found'] = True
                            watchlist[item['house_id']]['last_checked'] = datetime.now().isoformat()
                            save_watchlist()

                        matches_found.append({
                            'house_id': item['house_id'],
                            'ad_id': item['ad_id'],
                            'match_type': 'exact' if ftp_results['exact'] else 'partial',
                            'matches': ftp_results['exact'] or ftp_results['partial']
                        })
                        break
                else:
                    with watchlist_lock:
                        watchlist[item['house_id']]['last_checked'] = datetime.now().isoformat()
                        save_watchlist()

            print(f"Watchlist check complete. Found {len(matches_found)} matches.")

        except Exception as e:
            print(f"Error checking watchlist: {e}")
            traceback.print_exc()
        finally:
            watchlist_checking = False

    threading.Thread(target=check_watchlist_task, daemon=True).start()

    return jsonify({'success': True, 'message': 'Watchlist check started'})

@app.route('/api/manual_search', methods=['POST'])
def manual_search():
    """Manually search FTP index for files"""
    data = request.json
    search_term = data.get('search_term', '').strip()

    if not search_term:
        return jsonify({'error': 'Search term is required'}), 400

    try:
        # Search without source filter to get all results
        ftp_results = search_ftp_index(search_term, None)

        # Combine all results
        all_results = []
        all_results.extend(ftp_results.get('exact', []))
        all_results.extend(ftp_results.get('partial', []))
        all_results.extend(ftp_results.get('fuzzy', []))

        # Limit to 50 results
        all_results = all_results[:50]

        return jsonify({
            'success': True,
            'results': all_results,
            'count': len(all_results)
        })

    except Exception as e:
        print(f"Manual search error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

def periodic_watchlist_check():
    """Periodically check watchlist for new matches"""
    while True:
        time.sleep(3600)  # Check every hour
        try:
            with watchlist_lock:
                items_to_check = [item for item in watchlist.values() if not item.get('exclude', False) and not item.get('match_found', False)]

            if items_to_check:
                print(f"Performing periodic watchlist check for {len(items_to_check)} items...")

                for item in items_to_check:
                    search_terms = [item['ad_id'], item['house_id']]
                    search_terms = [term for term in search_terms if term and term.strip()]

                    for search_term in search_terms:
                        ftp_results = search_ftp_index(search_term, item.get('source'))

                        if ftp_results['exact'] or ftp_results['partial']:
                            with watchlist_lock:
                                watchlist[item['house_id']]['match_found'] = True
                                watchlist[item['house_id']]['last_checked'] = datetime.now().isoformat()
                                save_watchlist()

                            print(f"  ✓ Match found for {item['house_id']}")
                            # TODO: Push notification here
                            break
                    else:
                        with watchlist_lock:
                            watchlist[item['house_id']]['last_checked'] = datetime.now().isoformat()
                            save_watchlist()

                print("Periodic watchlist check complete.")

        except Exception as e:
            print(f"Error in periodic watchlist check: {e}")
            traceback.print_exc()

if __name__ == '__main__':
    # Try loading pre-built index from disk (written by ftp_indexer.py)
    if not load_ftp_index_from_disk():
        print("No pre-built index found, building from FTP (run ftp_indexer.py for faster startups)...")
        threading.Thread(target=build_ftp_index, daemon=True).start()
    else:
        print(f"Loaded pre-built index: {len(ftp_index)} files ready")

    # Start periodic watchlist checking
    print("Starting periodic watchlist checker...")
    threading.Thread(target=periodic_watchlist_check, daemon=True).start()

    print("\n" + "="*60)
    print("DUB Automation System Starting")
    print("="*60)
    print(f"Upload Directory: {BASE_UPLOAD_DIR}")
    print(f"Download Directory: {DOWNLOAD_DIR}")
    print(f"Watch Folder: {WATCH_FOLDER}")
    print(f"Reports Folder: {REPORTS_FOLDER}")
    print(f"Index File: {INDEX_FILE}")
    print(f"Server: http://0.0.0.0:8500")
    print("="*60 + "\n")

    app.run(debug=True, host='0.0.0.0', port=9900, use_reloader=False, threaded=True)
