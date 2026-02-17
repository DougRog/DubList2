#!/bin/bash

# DUB Automation System Startup Script
# Author: Doug - Lilly Broadcasting

echo "======================================"
echo "  DUB Automation System - Starting"
echo "======================================"
echo ""

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.8 or higher."
    exit 1
fi
echo "✓ Python 3 found"

# Check for ffprobe
if ! command -v ffprobe &> /dev/null; then
    echo "⚠️  Warning: ffprobe not found. Duration validation will not work."
    echo "   Install FFmpeg to enable duration validation: sudo apt-get install ffmpeg"
else
    echo "✓ ffprobe found"
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
fi

# Activate virtual environment
echo ""
echo "Activating virtual environment..."
source venv/bin/activate
echo "✓ Virtual environment activated"

# Install requirements
echo ""
echo "Installing/updating dependencies..."
pip install -q -r requirements.txt
echo "✓ Dependencies installed"

# Create required directories
echo ""
echo "Checking directories..."
mkdir -p /tmp/dub_uploads
mkdir -p /tmp/dub_data
# Note: User will need to create these with appropriate permissions
# mkdir -p /mnt/ingest/Dubs/Downloaded
# mkdir -p /mnt/ingest/Dubs/Encode_Watch
echo "✓ Directories ready"

echo ""
echo "======================================"
echo "  Starting DUB Automation System"
echo "======================================"
echo ""
echo "🌐 Web Interface: http://localhost:8000"
echo "🛑 Press Ctrl+C to stop"
echo ""

# Start the application
python3 dub_automation.py
