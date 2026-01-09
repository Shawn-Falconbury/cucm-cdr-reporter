# CUCM CDR Failed Call Reporter

A Python-based solution for monitoring and reporting failed calls from Cisco Unified Communications Manager (CUCM). This tool pulls CDR (Call Detail Records) data via SFTP, analyzes failed calls, and generates comprehensive PDF and email reports.

## Features

- **SFTP Integration**: Securely connects to CUCM CDR repository
- **Intelligent Parsing**: Parses CUCM CDR flat files with comprehensive field mapping
- **Failed Call Detection**: Identifies failed calls based on Q.931 cause codes and call duration
- **SQLite Database**: Stores historical data with configurable retention (default: 7 days)
- **Duplicate Detection**: Prevents reprocessing of already analyzed CDR files
- **PDF Reports**: Professional PDF reports with tables and statistics
- **HTML Email**: Formatted email reports with optional PDF attachment
- **Flexible Scheduling**: Can be run via cron or Task Scheduler

## Prerequisites

- Python 3.8 or higher
- Network access to CUCM publisher via SFTP (port 22)
- CDR billing enabled on CUCM
- SMTP server for email delivery

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Shawn-Falconbury/cucm-cdr-reporter.git
   cd cucm-cdr-reporter
   ```

2. **Create a virtual environment (recommended):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # Linux/Mac
   # or
   venv\Scripts\activate     # Windows
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## CUCM Configuration

Before using this tool, ensure CUCM is configured for CDR data collection:

### 1. Enable CDR Service
```
CUCM Administration → System → Service Parameters → CDR Enabled Flat Store
Set to: True
```

### 2. Configure CDR File Delivery Interval
```
CUCM Administration → System → Service Parameters → CDR File Interval
Recommended: 1-15 minutes for near real-time reporting
```

### 3. Create CDR Billing User
```
CUCM Administration → System → Security → Credential Policy
Create a user with CDR billing privileges

Or use CLI:
admin: utils cdr_billing set user <username> <password>
```

## Configuration

1. **Copy the sample configuration:**
   ```bash
   cp config.sample.json config.json
   ```

2. **Edit config.json with your settings:**
   ```json
   {
     "cucm_host": "cucm-pub.yourcompany.com",
     "cucm_port": 22,
     "cucm_username": "cdr_billing_user",
     "cucm_password": "your_secure_password",
     "cucm_cdr_path": "/var/log/active/cm/cdr_repository/processed",
     "smtp_server": "smtp.yourcompany.com",
     "smtp_port": 587,
     "email_from": "cucm-reports@yourcompany.com",
     "email_to": ["admin@yourcompany.com"],
     "cluster_name": "Production CUCM Cluster"
   }
   ```

## Usage

### Basic Usage
```bash
python cucm_cdr_reporter.py -c config.json
```

### Command Line Options
```bash
python cucm_cdr_reporter.py --help

Options:
  -c, --config CONFIG   Path to configuration file
  --init                Create sample configuration file
  --skip-fetch          Skip SFTP fetch, use existing data
  -v, --verbose         Enable verbose logging
```

### Testing with Sample Data
```bash
# Generate test CDR files
python generate_test_data.py --hours 24 --calls 50 --failure-rate 0.15

# Run report with test data (no CUCM connection needed)
python cucm_cdr_reporter.py -c config.json --skip-fetch
```

## Scheduling

### Linux (Cron)
```bash
# Run daily at 7 AM
0 7 * * * /path/to/venv/bin/python /path/to/cucm_cdr_reporter.py -c /path/to/config.json
```

### Windows (Task Scheduler)
1. Create Basic Task with daily/hourly trigger
2. Action: Start a program
   - Program: `C:\path\to\venv\Scripts\python.exe`
   - Arguments: `cucm_cdr_reporter.py -c config.json`

## Understanding Q.931 Cause Codes

| Code | Description |
|------|-------------|
| 1 | Unallocated number |
| 3 | No route to destination |
| 17 | User busy |
| 18 | No user responding |
| 19 | No answer |
| 21 | Call rejected |
| 27 | Destination out of order |
| 28 | Invalid number format |
| 34 | No circuit available |
| 38 | Network out of order |

## Project Structure

```
cucm-cdr-reporter/
├── cucm_cdr_reporter.py    # Main application
├── generate_test_data.py   # Test data generator
├── requirements.txt        # Python dependencies
├── config.sample.json      # Configuration template
├── .gitignore             # Git ignore rules
└── README.md              # This file
```

## Security Notes

- Never commit `config.json` with real credentials
- Use environment variables for sensitive data in production
- Consider SSH keys instead of password authentication
- Limit CDR billing user permissions to read-only

## License

MIT License

## Contributing

Pull requests welcome! Please ensure any changes maintain backward compatibility with existing configurations.
