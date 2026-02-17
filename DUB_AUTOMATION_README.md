# DUB Automation System

A comprehensive commercial dubbing automation workflow that combines FTP search, file validation, download queue management, and reporting.

## Features

### 🎯 Core Functionality

1. **DUB List Upload & Parsing**
   - Upload .dub or .txt files (drag & drop supported)
   - Parses fixed-width format (columns 9-259)
   - Extracts: House ID, Ad ID, Duration, Source
   - Source name mapping (abbreviated → full names)

2. **Automatic FTP Search**
   - Builds searchable index of FTP server content
   - Searches by Ad ID and House ID
   - Three-tier matching:
     - **Exact**: Perfect filename match
     - **Partial**: Contains search term
     - **Fuzzy**: 75%+ similarity score

3. **User Review & Approval**
   - View all matches for each spot
   - Approve individual files
   - Bulk approve all exact matches
   - Manual search for missing files

4. **Download Queue Management**
   - Real-time status updates
   - Progress bars for each download
   - Status tracking: Queued → Downloading → Validating → Completed

5. **File Validation**
   - Uses ffprobe to check actual video duration
   - Compares against expected duration from DUB list
   - Flags mismatches (±1 second tolerance)

6. **Automatic File Handling**
   - Renames files using House ID
   - Transfers to watch folder for encoding
   - Maintains complete audit trail

7. **Comprehensive Reporting**
   - Successfully Processed files
   - Missing files
   - Duration Mismatches
   - Summary statistics
   - Exportable HTML reports

## Installation

### Prerequisites

```bash
# Python 3.8+
python3 --version

# ffprobe (part of FFmpeg)
ffprobe -version

# Required Python packages
pip install flask werkzeug
```

### Directory Setup

The system automatically creates these directories:

```
/mnt/ingest/Dubs/
├── Uploads/          # Uploaded DUB files
├── Downloaded/       # Downloaded files (temp)
├── Watch/            # Final renamed files for encoding
├── Data/             # Session data and mappings
└── Reports/          # Generated reports
```

### Configuration

Edit these settings in `dub_automation.py`:

```python
# FTP Configuration
FTP_HOST = '192.168.0.198'
FTP_PORT = 21
FTP_USER = 'creative'
FTP_PASS = 'your-password'
FTP_SEARCH_DIRS = ['/MDSDublist-Commercial_Content_Upload', '/Lilly_Archive']

# Directory Configuration
BASE_UPLOAD_DIR = '/mnt/ingest/Dubs/Uploads'
DOWNLOAD_DIR = '/mnt/ingest/Dubs/Downloaded'
WATCH_FOLDER = '/mnt/ingest/Dubs/Watch'
DATA_FOLDER = '/mnt/ingest/Dubs/Data'
REPORTS_FOLDER = '/mnt/ingest/Dubs/Reports'
```

## Usage

### Starting the Server

```bash
python3 dub_automation.py
```

The server starts on **http://0.0.0.0:7020**

### Workflow

1. **Upload DUB List**
   - Click upload zone or drag & drop .dub/.txt file
   - System automatically parses and searches FTP

2. **Review Matches**
   - View search results for each spot
   - See exact/partial/fuzzy matches
   - Approve matches one-by-one or bulk approve exact matches

3. **Monitor Queue**
   - Watch real-time download progress
   - See validation status
   - Track completion

4. **Generate Report**
   - Click "Generate Report" button
   - Review processed, missing, and mismatched files
   - Download HTML report for records

## DUB File Format

Fixed-width format with the following columns:

- **Columns 9-41**: House ID
- **Columns 191-241**: Ad ID (filename on FTP)
- **Columns 241-249**: Duration
- **Columns 249-259**: Source (abbreviated)

Example line:
```
        HOUSEID12345                              FILENAME123456                      :30      ERIECREATI
```

## Troubleshooting

### FTP Connection Issues
- Verify FTP credentials in configuration
- Check network connectivity
- Ensure FTP server allows multiple connections

### ffprobe Not Found
```bash
# Install FFmpeg (includes ffprobe)
sudo apt-get install ffmpeg  # Debian/Ubuntu
brew install ffmpeg           # macOS
```

### Permission Issues
```bash
# Ensure directories are writable
chmod -R 755 /mnt/ingest/Dubs/
```

### Missing Matches
- Refresh FTP index (may be stale)
- Check if file exists on FTP server
- Try manual search with different terms
- Verify filename format matches Ad ID

## License

Copyright © 2025 Lilly Broadcasting. All rights reserved.

---

**Version:** 1.0.0  
**Last Updated:** January 2025
