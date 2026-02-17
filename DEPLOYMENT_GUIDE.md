# 🚀 DUB Automation System - Deployment Guide

## Quick Deploy (5 Minutes)

### 1. Upload Files to Server
```bash
# Copy entire directory to your server
scp -r dub-automation/ user@server:/opt/

# Or if already on server, navigate to directory
cd /opt/dub-automation
```

### 2. Update Configuration
```bash
# Edit the main application file
nano dub_automation.py

# Update these lines (around line 17-22):
FTP_HOST = '192.168.0.198'        # ← Your FTP server IP
FTP_USER = 'creative'              # ← Your FTP username  
FTP_PASS = 'your-password-here'    # ← Your FTP password

# Update directory paths if needed (around line 10-14):
BASE_UPLOAD_DIR = '/mnt/ingest/Dubs/Uploads'
DOWNLOAD_DIR = '/mnt/ingest/Dubs/Downloaded'
WATCH_FOLDER = '/mnt/ingest/Dubs/Watch'
```

### 3. Install Dependencies
```bash
# Make start script executable
chmod +x start.sh

# Run startup script (handles everything)
./start.sh
```

### 4. Access Web Interface
```
Open browser to: http://your-server-ip:7020
```

## ✅ Pre-Flight Checklist

Before going live, verify:

- [ ] Python 3.8+ installed (`python3 --version`)
- [ ] FFmpeg installed (`ffprobe -version`)
- [ ] FTP credentials are correct (test with FileZilla)
- [ ] All directories exist and are writable:
  - [ ] `/mnt/ingest/Dubs/Uploads`
  - [ ] `/mnt/ingest/Dubs/Downloaded`  
  - [ ] `/mnt/ingest/Dubs/Watch`
  - [ ] `/mnt/ingest/Dubs/Data`
  - [ ] `/mnt/ingest/Dubs/Reports`
- [ ] Watch folder is monitored by encoding system
- [ ] Network allows FTP connections

## 📁 Directory Structure
```
/opt/dub-automation/
├── dub_automation.py       ← Main application (edit config here)
├── templates/
│   └── dub_automation.html ← Web interface
├── start.sh                ← Startup script
├── requirements.txt        ← Python dependencies
├── venv/                   ← Virtual environment (auto-created)
└── *.md                    ← Documentation files

/mnt/ingest/Dubs/
├── Uploads/                ← DUB files land here
├── Downloaded/             ← Temp download location
├── Watch/                  ← Renamed files for encoding
├── Data/                   ← Session data & mappings
└── Reports/                ← Generated reports
```

## 🎯 First Test Run

1. **Start the system**
   ```bash
   ./start.sh
   ```

2. **Upload test DUB file**
   - Open http://localhost:7020
   - Drag & drop a small DUB file (5-10 spots)

3. **Verify FTP search**
   - Should see "Searching FTP..." message
   - Check search results for each spot

4. **Approve one spot**
   - Click "Approve" on an exact match
   - Watch queue section appear
   - Monitor download progress

5. **Check watch folder**
   ```bash
   ls -lh /mnt/ingest/Dubs/Watch/
   ```
   - Should see renamed file: `HOUSEID.mov`

6. **Generate report**
   - Click "Generate Report" button
   - Download and review HTML report

## ⚙️ Production Deployment

### Option A: Run with systemd (Recommended)

```bash
# Create service file
sudo nano /etc/systemd/system/dub-automation.service
```

Paste this configuration:
```ini
[Unit]
Description=DUB Automation System
After=network.target

[Service]
Type=simple
User=broadcast
WorkingDirectory=/opt/dub-automation
ExecStart=/opt/dub-automation/venv/bin/python3 /opt/dub-automation/dub_automation.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/dub-automation.log
StandardError=append:/var/log/dub-automation-error.log

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable dub-automation
sudo systemctl start dub-automation
sudo systemctl status dub-automation
```

View logs:
```bash
sudo journalctl -u dub-automation -f
```

### Option B: Run with screen (Quick/Dev)

```bash
# Start in screen session
screen -S dub-automation
./start.sh

# Detach: Ctrl+A, then D
# Reattach: screen -r dub-automation
```

### Option C: Run with tmux

```bash
# Start in tmux session
tmux new -s dub-automation
./start.sh

# Detach: Ctrl+B, then D
# Reattach: tmux attach -t dub-automation
```

## 🌐 Network Access

### Local Network Only (Default)
```
Access from: http://192.168.x.x:7020
```

### Add Nginx Reverse Proxy (Production)

```bash
# Install nginx
sudo apt-get install nginx

# Create config
sudo nano /etc/nginx/sites-available/dub-automation
```

```nginx
server {
    listen 80;
    server_name dub-automation.example.com;
    
    location / {
        proxy_pass http://127.0.0.1:7020;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

```bash
# Enable site
sudo ln -s /etc/nginx/sites-available/dub-automation /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## 🔐 Security Hardening

### 1. File Permissions
```bash
# Set proper ownership
sudo chown -R broadcast:broadcast /opt/dub-automation
sudo chown -R broadcast:broadcast /mnt/ingest/Dubs

# Set directory permissions
sudo chmod 755 /opt/dub-automation
sudo chmod 775 /mnt/ingest/Dubs/*
```

### 2. Firewall Rules
```bash
# Allow port 7020 only from local network
sudo ufw allow from 192.168.0.0/16 to any port 7020
sudo ufw enable
```

### 3. Secure FTP Credentials
```bash
# Store credentials in separate config file
nano /etc/dub-automation/config.py
```

```python
FTP_HOST = '192.168.0.198'
FTP_USER = 'creative'
FTP_PASS = 'your-secure-password'
```

```bash
# Protect config file
sudo chmod 600 /etc/dub-automation/config.py
```

Update `dub_automation.py` to import from config.

## 📊 Monitoring

### Check System Status
```bash
# Service status
sudo systemctl status dub-automation

# View logs
tail -f /var/log/dub-automation.log

# Check for errors
grep ERROR /var/log/dub-automation.log

# Monitor disk space
df -h /mnt/ingest/Dubs/

# Check running processes
ps aux | grep dub_automation
```

### Set Up Alerts (Optional)
```bash
# Install monitoring tools
sudo apt-get install monit

# Configure alerts for:
# - Service down
# - Disk space low
# - High CPU usage
```

## 🔄 Maintenance

### Daily Tasks
- Monitor queue for stuck jobs
- Check reports folder size
- Review error logs

### Weekly Tasks  
- Clean up old session data
- Archive old reports
- Verify FTP index accuracy

### Monthly Tasks
- Review source name mappings
- Update Python dependencies
- Check for system updates

## 📞 Quick Reference

### Important Commands
```bash
# Start system
./start.sh

# Stop system (if using systemd)
sudo systemctl stop dub-automation

# Restart system
sudo systemctl restart dub-automation

# View logs
sudo journalctl -u dub-automation -f

# Check disk space
df -h /mnt/ingest/Dubs/

# Clean old downloads
find /mnt/ingest/Dubs/Downloaded -mtime +7 -delete
```

### Common URLs
- **Web Interface**: http://server:7020
- **Queue Status**: http://server:7020/api/queue_status
- **FTP Refresh**: http://server:7020/api/refresh_ftp_index (POST)

### Key Files to Back Up
```
/opt/dub-automation/dub_automation.py    (your config)
/mnt/ingest/Dubs/Data/source_mappings.json
/mnt/ingest/Dubs/Reports/*.html
```

## 🆘 Emergency Procedures

### System Won't Start
```bash
# Check Python
python3 --version

# Check dependencies
pip list | grep -i flask

# Check ports
sudo netstat -tlnp | grep 7020

# Check permissions
ls -la /opt/dub-automation
```

### FTP Connection Issues
```bash
# Test FTP manually
ftp 192.168.0.198
# login with credentials

# Check network
ping 192.168.0.198
telnet 192.168.0.198 21
```

### Disk Full
```bash
# Check space
df -h

# Clean old downloads
rm -rf /mnt/ingest/Dubs/Downloaded/*

# Clean old reports (keep last 30 days)
find /mnt/ingest/Dubs/Reports -mtime +30 -delete
```

### Reset System
```bash
# Stop service
sudo systemctl stop dub-automation

# Clear session data
rm -rf /mnt/ingest/Dubs/Data/session_*

# Clear downloads
rm -rf /mnt/ingest/Dubs/Downloaded/*

# Restart
sudo systemctl start dub-automation
```

## 📚 Documentation Files

- **SYSTEM_OVERVIEW.md** - Complete system explanation with examples
- **DUB_AUTOMATION_README.md** - Technical documentation
- **QUICKSTART.md** - Quick getting started guide (if available)
- **README.md** - General system information

## ✨ You're Ready!

Your DUB Automation System is now deployed and ready to use.

**Next Steps:**
1. Run a test with a small DUB file
2. Train your team on the workflow
3. Monitor the first few production runs
4. Set up automated backups
5. Schedule FTP index refreshes

**Need Help?**
- Check SYSTEM_OVERVIEW.md for detailed examples
- Review logs for error messages
- Contact broadcast engineering team

---

**Deployment Date**: _____________  
**Deployed By**: _____________  
**Server**: _____________  
**Notes**: _____________

**System is GO! 🚀**
