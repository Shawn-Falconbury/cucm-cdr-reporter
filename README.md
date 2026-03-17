# CUCM CDR Failed Call Reporter

Analyzes Cisco Unified Communications Manager (CUCM) Call Detail Records to identify failed calls, generates PDF reports with failure statistics, and delivers them via email.

**Version:** 1.2.2  
**License:** MIT  
**Python:** 3.12+  
**FIPS Compliant:** Yes (SHA256 hashing)

---

## Quick Reference

```bash
# Generate sample config file
python cucm_cdr_reporter.py --init

# Production: Pull CDR files via SFTP and generate report
python cucm_cdr_reporter.py -c config.json

# Process local CDR files (no SFTP connection)
python cucm_cdr_reporter.py -c config.json --process-local

# Regenerate report from existing database (no file processing)
python cucm_cdr_reporter.py -c config.json --skip-fetch

# Verbose output for troubleshooting
python cucm_cdr_reporter.py -c config.json -v
```

### Command Line Options

| Option | Description |
|--------|-------------|
| `-c, --config FILE` | Path to JSON configuration file (required) |
| `--init` | Generate sample `config.json` template |
| `--process-local` | Process CDR files from `local_cdr_dir` without SFTP |
| `--skip-fetch` | Skip file processing, generate report from existing database |
| `-v, --verbose` | Enable debug-level logging |

---

## Installation

### Requirements

```bash
pip install paramiko reportlab
```

### Dependencies

| Package | Purpose |
|---------|---------|
| `paramiko` | SFTP connectivity to file server |
| `reportlab` | PDF report generation |

Standard library modules used: `sqlite3`, `csv`, `smtplib`, `hashlib`, `json`, `logging`

---

## Configuration

Create a configuration file using `--init` or manually create `config.json`:

```json
{
  "cucm_host": "fileserver.example.com",
  "cucm_port": 22,
  "cucm_username": "cdr_user",
  "cucm_password": "secure_password",
  "cucm_cdr_path": "/path/to/cdr/files",
  "local_cdr_dir": "./cdr_files",
  "database_path": "./cdr_database.db",
  "report_output_dir": "./reports",
  "hours_to_analyze": 24,
  "retention_days": 7,
  "smtp_server": "smtp.example.com",
  "smtp_port": 587,
  "smtp_username": "reports@example.com",
  "smtp_password": "smtp_password",
  "smtp_use_tls": true,
  "email_from": "cucm-reports@example.com",
  "email_to": ["admin@example.com", "voip-team@example.com"],
  "email_subject": "CUCM Failed Calls Report - {date}",
  "cluster_name": "Production CUCM Cluster"
}
```

### Configuration Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `cucm_host` | string | SFTP server hostname (file server receiving CDR pushes from CUCM) |
| `cucm_port` | int | SFTP port (default: 22) |
| `cucm_username` | string | SFTP username |
| `cucm_password` | string | SFTP password |
| `cucm_cdr_path` | string | Remote directory containing CDR files |
| `local_cdr_dir` | string | Local directory for downloaded/local CDR files |
| `database_path` | string | SQLite database file path |
| `report_output_dir` | string | Directory for generated PDF reports |
| `hours_to_analyze` | int | Analysis window in hours (default: 24) |
| `retention_days` | int | Days to retain database records and reports (default: 7) |
| `smtp_server` | string | SMTP server for email delivery |
| `smtp_port` | int | SMTP port (587 for TLS, 25 for plain) |
| `smtp_username` | string | SMTP authentication username |
| `smtp_password` | string | SMTP authentication password |
| `smtp_use_tls` | bool | Enable STARTTLS encryption |
| `email_from` | string | Sender email address |
| `email_to` | list | List of recipient email addresses |
| `email_subject` | string | Email subject (`{date}` placeholder available) |
| `cluster_name` | string | Display name for the CUCM cluster in reports |

> **Note:** Use forward slashes (`/`) in JSON paths, or escape backslashes (`\\`).

---

## Operating Modes

### Mode 1: SFTP Pull (Default)

```bash
python cucm_cdr_reporter.py -c config.json
```

**Workflow:**
1. Connects to file server via SFTP
2. Downloads CDR files from `cucm_cdr_path` modified within `hours_to_analyze`
3. Parses files and identifies failed calls
4. Stores results in SQLite database
5. Generates PDF report and emails it
6. Cleans up old records based on `retention_days`

**Use when:** CUCM pushes CDR files to a file server, and the script pulls from that server.

### Mode 2: Local Processing

```bash
python cucm_cdr_reporter.py -c config.json --process-local
```

**Workflow:**
1. Reads CDR files from `local_cdr_dir` directory
2. Parses files and identifies failed calls
3. Stores results in SQLite database
4. Generates PDF report and emails it
5. Cleans up old records

**Use when:** CDR files are already available locally, or for testing with sample data.

### Mode 3: Report Only

```bash
python cucm_cdr_reporter.py -c config.json --skip-fetch
```

**Workflow:**
1. Skips all file processing
2. Generates report from existing database records
3. Emails the report

**Use when:** Regenerating a report without reprocessing files.

---

## Report Contents

### PDF Report (2 pages)

**Page 1 - Executive Summary:**
- Total failed calls count
- Analysis period
- Failures by Q.931 cause code (with percentages)
- Top 10 devices with failures

**Page 2 - Call Detail:**
- Most recent 50 failed calls
- Columns: Time, From, To, Cause Code, Device, Origin IP

### Email Report

HTML-formatted email body containing:
- Summary statistics
- High failure count alert (when > 100 failures)
- Failures by cause code
- Top devices with failures
- PDF report attached

---

## Database Schema

SQLite database with automatic retention cleanup.

### Table: `failed_calls`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `global_call_id` | TEXT | Unique call identifier |
| `date_time_origination` | TIMESTAMP | Call start time |
| `calling_party_number` | TEXT | Caller number |
| `original_called_party_number` | TEXT | Dialed number |
| `final_called_party_number` | TEXT | Final destination number |
| `orig_cause_value` | INTEGER | Origination cause code |
| `dest_cause_value` | INTEGER | Destination cause code |
| `failure_reason` | TEXT | Human-readable failure reason |
| `orig_device_name` | TEXT | Originating device name |
| `dest_device_name` | TEXT | Destination device name |
| `orig_ip_addr` | TEXT | Origin IP address (dotted-decimal) |
| `dest_ip_addr` | TEXT | Destination IP address |
| `file_hash` | TEXT | SHA256 hash of source file |

### Table: `processed_files`

Tracks processed files to prevent duplicate processing.

| Column | Type | Description |
|--------|------|-------------|
| `filename` | TEXT | CDR filename |
| `file_hash` | TEXT | SHA256 hash for change detection |
| `records_processed` | INTEGER | Total records in file |
| `failed_calls_found` | INTEGER | Failed calls extracted |
| `processed_at` | TIMESTAMP | Processing timestamp |

---

## Scheduling with Cron

### Daily Report at 6:00 AM

```cron
0 6 * * * cd /path/to/cucm-cdr-reporter && /usr/bin/python3 cucm_cdr_reporter.py -c config.json >> /var/log/cucm_cdr_reporter.log 2>&1
```

### Hourly Processing

```cron
0 * * * * cd /path/to/cucm-cdr-reporter && /usr/bin/python3 cucm_cdr_reporter.py -c config.json >> /var/log/cucm_cdr_reporter.log 2>&1
```

---

## Failed Call Detection

A call is classified as **failed** when:
- Call duration is 0 seconds, AND
- Cause code is not in the success list (0, 16, 393216)

### Common Q.931 Cause Codes

| Code | Description | Common Cause |
|------|-------------|--------------|
| 1 | Unallocated number | Invalid or non-existent number dialed |
| 17 | User busy | Called party is on another call |
| 18 | No user responding | Phone not registered or offline |
| 19 | No answer | Call rang but was not answered |
| 21 | Call rejected | Called party rejected the call |
| 27 | Destination out of order | Network/trunk issue |
| 28 | Invalid number format | Malformed dial string |
| 34 | No circuit available | All trunks busy |
| 38 | Network out of order | WAN/network failure |
| 41 | Temporary failure | Transient system issue |
| 42 | Switching equipment congestion | System overloaded |
| 47 | Resource unavailable | CUCM resource exhaustion |
| 127 | Interworking | Protocol mismatch between systems |

---

## IP Address Handling

CUCM stores IP addresses as 32-bit integers in little-endian byte order. The script automatically converts these to standard dotted-decimal notation:

```
CDR Value: 3232235876
Converted: 192.168.1.100
```

---

## Troubleshooting

### No failed calls found

1. Verify CDR files exist and have recent timestamps
2. Check `hours_to_analyze` setting matches your CDR age
3. Run with `-v` to see detailed processing logs
4. Confirm CDR files follow `cdr_*` naming convention

### SFTP connection failed

1. Verify hostname, port, username, password
2. Test connectivity: `sftp user@host`
3. Check firewall rules
4. Verify the user has read access to `cucm_cdr_path`

### Database errors after upgrade

When upgrading versions that change the database schema or hash algorithm:

```bash
rm cdr_database.db
```

The database will be recreated on next run.

### FIPS mode errors

If you see `EVP_DigestInit_ex disabled for FIPS`:
- Ensure you're using version 1.2.2+ (uses SHA256 instead of MD5)

### Email not sending

1. Verify SMTP settings
2. Check `smtp_use_tls` matches your server requirements
3. Confirm `email_to` contains valid addresses
4. Review logs for SMTP error messages

---

## File Structure

```
cucm-cdr-reporter/
├── cucm_cdr_reporter.py    # Main script
├── config.json             # Configuration file
├── cdr_database.db         # SQLite database (auto-created)
├── cucm_cdr_reporter.log   # Application log
├── cdr_files/              # Local CDR file storage
│   └── cdr_*.txt
└── reports/                # Generated PDF reports
    └── cucm_failed_calls_*.pdf
```

---

## Changelog

### v1.2.2
- Fixed IP address byte order conversion (little-endian)
- Added Origin IP column to PDF report detail table

### v1.2.1
- Changed hash algorithm from MD5 to SHA256 (FIPS compliance)
- Added Origin IP column to report

### v1.2.0
- Added `--process-local` flag for local file processing
- Improved CLI help documentation

### v1.1.0
- Fixed Python 3.12 SQLite datetime deprecation warnings
- Added custom datetime adapters for ISO format storage

### v1.0.0
- Initial release
- SFTP-based CDR retrieval
- PDF and HTML report generation
- Email delivery with PDF attachment
- SQLite storage with retention management
