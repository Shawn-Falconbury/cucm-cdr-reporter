#!/usr/bin/env python3
"""
CUCM CDR Failed Call Reporter
=============================
Pulls CDR data from Cisco Unified Communications Manager via SFTP,
analyzes failed calls, and generates PDF/email reports.

Version: 1.0.0
License: MIT
"""

import os
import sys
import csv
import sqlite3
import logging
import smtplib
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
import json

# Third-party imports
import paramiko
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, Image
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('cucm_cdr_reporter.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class Config:
    """Configuration settings for the CDR Reporter"""
    
    # CUCM SFTP Settings
    cucm_host: str = ""
    cucm_port: int = 22
    cucm_username: str = ""
    cucm_password: str = ""
    cucm_cdr_path: str = "/var/log/active/cm/cdr_repository/processed"
    
    # Local Storage
    local_cdr_dir: str = "./cdr_files"
    database_path: str = "./cdr_database.db"
    
    # Report Settings
    report_output_dir: str = "./reports"
    hours_to_analyze: int = 24
    retention_days: int = 7
    
    # Email Settings
    smtp_server: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    email_from: str = ""
    email_to: List[str] = field(default_factory=list)
    email_subject: str = "CUCM Failed Calls Report - {date}"
    
    # Cluster Info
    cluster_name: str = "CUCM Cluster"
    
    @classmethod
    def from_file(cls, config_path: str) -> 'Config':
        """Load configuration from JSON file"""
        with open(config_path, 'r') as f:
            data = json.load(f)
        return cls(**data)
    
    def to_file(self, config_path: str):
        """Save configuration to JSON file"""
        with open(config_path, 'w') as f:
            json.dump(self.__dict__, f, indent=2)


# =============================================================================
# Q.931 CAUSE CODES
# =============================================================================

CAUSE_CODES = {
    0: "No error",
    1: "Unallocated/unassigned number",
    2: "No route to specified transit network",
    3: "No route to destination",
    4: "Send special information tone",
    5: "Misdialed trunk prefix",
    6: "Channel unacceptable",
    7: "Call awarded and being delivered",
    16: "Normal call clearing",
    17: "User busy",
    18: "No user responding",
    19: "No answer from user (user alerted)",
    20: "Subscriber absent",
    21: "Call rejected",
    22: "Number changed",
    23: "Redirection to new destination",
    25: "Exchange routing error",
    26: "Non-selected user clearing",
    27: "Destination out of order",
    28: "Invalid number format",
    29: "Facility rejected",
    30: "Response to STATUS ENQUIRY",
    31: "Normal, unspecified",
    34: "No circuit/channel available",
    38: "Network out of order",
    39: "Permanent frame mode connection out of service",
    40: "Permanent frame mode connection operational",
    41: "Temporary failure",
    42: "Switching equipment congestion",
    43: "Access information discarded",
    44: "Requested circuit/channel not available",
    46: "Precedence call blocked",
    47: "Resource unavailable, unspecified",
    49: "Quality of service not available",
    50: "Requested facility not subscribed",
    52: "Outgoing calls barred",
    54: "Incoming calls barred",
    57: "Bearer capability not authorized",
    58: "Bearer capability not presently available",
    62: "Inconsistency in designated outgoing access",
    63: "Service or option not available",
    65: "Bearer capability not implemented",
    66: "Channel type not implemented",
    69: "Requested facility not implemented",
    70: "Only restricted digital bearer capability available",
    79: "Service or option not implemented",
    81: "Invalid call reference value",
    82: "Identified channel does not exist",
    83: "Suspended call exists but call identity does not",
    84: "Call identity in use",
    85: "No call suspended",
    86: "Call having requested identity has been cleared",
    87: "User not member of CUG",
    88: "Incompatible destination",
    90: "Non-existent CUG",
    91: "Invalid transit network selection",
    95: "Invalid message, unspecified",
    96: "Mandatory IE missing",
    97: "Message type non-existent",
    98: "Message not compatible with call state",
    99: "IE non-existent or not implemented",
    100: "Invalid IE contents",
    101: "Message not compatible with call state",
    102: "Recovery on timer expiry",
    103: "Parameter non-existent or not implemented",
    110: "Message with unrecognized parameter discarded",
    111: "Protocol error, unspecified",
    127: "Interworking, unspecified",
    # Cisco-specific codes
    393216: "Normal clearing (Cisco)",
    458752: "Call rejected (Cisco)",
}

# Cause codes that indicate a failed call (not normal completion)
FAILED_CAUSE_CODES = {
    1, 2, 3, 17, 18, 19, 20, 21, 22, 27, 28, 29, 31, 34, 38, 41, 42, 43, 44,
    46, 47, 49, 50, 52, 54, 57, 58, 63, 65, 66, 69, 79, 88, 95, 96, 97, 98,
    99, 100, 101, 102, 111, 127
}

# Successful call codes to exclude
SUCCESS_CODES = {0, 16, 393216}


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class CDRRecord:
    """Represents a single CDR record"""
    cdr_record_type: int = 0
    global_call_id: str = ""
    date_time_origination: datetime = None
    date_time_connect: datetime = None
    date_time_disconnect: datetime = None
    calling_party_number: str = ""
    calling_party_unicode_name: str = ""
    original_called_party_number: str = ""
    final_called_party_number: str = ""
    last_redirect_dn: str = ""
    orig_cause_value: int = 0
    dest_cause_value: int = 0
    duration: int = 0
    orig_device_name: str = ""
    dest_device_name: str = ""
    orig_ip_addr: str = ""
    dest_ip_addr: str = ""
    calling_party_number_partition: str = ""
    original_called_party_number_partition: str = ""
    final_called_party_number_partition: str = ""
    hunt_pilot_dn: str = ""
    hunt_pilot_partition: str = ""
    call_termination_on_behalf_of: int = 0
    orig_dtmf_method: int = 0
    dest_dtmf_method: int = 0
    mobile_calling_party_number: str = ""
    mobile_called_party_number: str = ""
    calling_party_device_type: str = ""
    final_called_party_device_type: str = ""
    orig_video_cap_bandwidth: int = 0
    dest_video_cap_bandwidth: int = 0
    
    @property
    def is_failed(self) -> bool:
        """Determine if this call was failed/unsuccessful"""
        if self.duration == 0:
            if self.dest_cause_value not in SUCCESS_CODES:
                return True
            if self.orig_cause_value not in SUCCESS_CODES:
                return True
        return False
    
    @property
    def failure_reason(self) -> str:
        """Get human-readable failure reason"""
        cause = self.dest_cause_value if self.dest_cause_value != 0 else self.orig_cause_value
        return CAUSE_CODES.get(cause, f"Unknown cause code: {cause}")
    
    @property
    def primary_cause_code(self) -> int:
        """Get the primary cause code for the failure"""
        return self.dest_cause_value if self.dest_cause_value != 0 else self.orig_cause_value


# =============================================================================
# DATABASE OPERATIONS
# =============================================================================

class CDRDatabase:
    """SQLite database handler for CDR storage and retention"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self._init_database()
    
    def _init_database(self):
        """Initialize database and create tables"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS failed_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                global_call_id TEXT,
                date_time_origination TIMESTAMP,
                calling_party_number TEXT,
                original_called_party_number TEXT,
                final_called_party_number TEXT,
                orig_cause_value INTEGER,
                dest_cause_value INTEGER,
                failure_reason TEXT,
                duration INTEGER,
                orig_device_name TEXT,
                dest_device_name TEXT,
                orig_ip_addr TEXT,
                dest_ip_addr TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                file_hash TEXT,
                UNIQUE(global_call_id, date_time_origination)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS processed_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE,
                file_hash TEXT,
                records_processed INTEGER,
                failed_calls_found INTEGER,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_failed_calls_datetime 
            ON failed_calls(date_time_origination)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_failed_calls_cause 
            ON failed_calls(dest_cause_value)
        ''')
        
        self.conn.commit()
    
    def is_file_processed(self, filename: str, file_hash: str) -> bool:
        """Check if a file has already been processed"""
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT id FROM processed_files WHERE filename = ? AND file_hash = ?',
            (filename, file_hash)
        )
        return cursor.fetchone() is not None
    
    def mark_file_processed(self, filename: str, file_hash: str, 
                           records_processed: int, failed_calls_found: int):
        """Mark a file as processed"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO processed_files 
            (filename, file_hash, records_processed, failed_calls_found, processed_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (filename, file_hash, records_processed, failed_calls_found, datetime.now()))
        self.conn.commit()
    
    def insert_failed_call(self, record: CDRRecord, file_hash: str):
        """Insert a failed call record"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO failed_calls 
                (global_call_id, date_time_origination, calling_party_number,
                 original_called_party_number, final_called_party_number,
                 orig_cause_value, dest_cause_value, failure_reason, duration,
                 orig_device_name, dest_device_name, orig_ip_addr, dest_ip_addr,
                 file_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                record.global_call_id,
                record.date_time_origination,
                record.calling_party_number,
                record.original_called_party_number,
                record.final_called_party_number,
                record.orig_cause_value,
                record.dest_cause_value,
                record.failure_reason,
                record.duration,
                record.orig_device_name,
                record.dest_device_name,
                record.orig_ip_addr,
                record.dest_ip_addr,
                file_hash
            ))
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass
    
    def get_failed_calls(self, hours: int = 24) -> List[Dict]:
        """Get failed calls from the last N hours"""
        cursor = self.conn.cursor()
        cutoff = datetime.now() - timedelta(hours=hours)
        
        cursor.execute('''
            SELECT * FROM failed_calls 
            WHERE date_time_origination >= ?
            ORDER BY date_time_origination DESC
        ''', (cutoff,))
        
        return [dict(row) for row in cursor.fetchall()]
    
    def get_failure_statistics(self, hours: int = 24) -> Dict:
        """Get aggregated failure statistics"""
        cursor = self.conn.cursor()
        cutoff = datetime.now() - timedelta(hours=hours)
        
        cursor.execute('''
            SELECT COUNT(*) as total FROM failed_calls 
            WHERE date_time_origination >= ?
        ''', (cutoff,))
        total = cursor.fetchone()['total']
        
        cursor.execute('''
            SELECT dest_cause_value, failure_reason, COUNT(*) as count 
            FROM failed_calls 
            WHERE date_time_origination >= ?
            GROUP BY dest_cause_value 
            ORDER BY count DESC
        ''', (cutoff,))
        by_cause = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute('''
            SELECT strftime('%Y-%m-%d %H:00', date_time_origination) as hour, 
                   COUNT(*) as count 
            FROM failed_calls 
            WHERE date_time_origination >= ?
            GROUP BY hour 
            ORDER BY hour
        ''', (cutoff,))
        by_hour = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute('''
            SELECT calling_party_number, COUNT(*) as count 
            FROM failed_calls 
            WHERE date_time_origination >= ? AND calling_party_number != ''
            GROUP BY calling_party_number 
            ORDER BY count DESC 
            LIMIT 10
        ''', (cutoff,))
        top_callers = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute('''
            SELECT original_called_party_number, COUNT(*) as count 
            FROM failed_calls 
            WHERE date_time_origination >= ? AND original_called_party_number != ''
            GROUP BY original_called_party_number 
            ORDER BY count DESC 
            LIMIT 10
        ''', (cutoff,))
        top_destinations = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute('''
            SELECT orig_device_name, COUNT(*) as count 
            FROM failed_calls 
            WHERE date_time_origination >= ? AND orig_device_name != ''
            GROUP BY orig_device_name 
            ORDER BY count DESC 
            LIMIT 10
        ''', (cutoff,))
        top_devices = [dict(row) for row in cursor.fetchall()]
        
        return {
            'total_failed_calls': total,
            'by_cause': by_cause,
            'by_hour': by_hour,
            'top_callers': top_callers,
            'top_destinations': top_destinations,
            'top_devices': top_devices,
            'analysis_period_hours': hours,
            'cutoff_time': cutoff.isoformat()
        }
    
    def cleanup_old_records(self, retention_days: int):
        """Remove records older than retention period"""
        cursor = self.conn.cursor()
        cutoff = datetime.now() - timedelta(days=retention_days)
        
        cursor.execute('''
            DELETE FROM failed_calls WHERE date_time_origination < ?
        ''', (cutoff,))
        
        deleted = cursor.rowcount
        self.conn.commit()
        logger.info(f"Cleaned up {deleted} records older than {retention_days} days")
        return deleted
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()


# =============================================================================
# SFTP OPERATIONS
# =============================================================================

class CDRFetcher:
    """Handles SFTP connection to CUCM and CDR file retrieval"""
    
    def __init__(self, config: Config):
        self.config = config
        self.sftp = None
        self.transport = None
    
    def connect(self):
        """Establish SFTP connection to CUCM"""
        logger.info(f"Connecting to CUCM at {self.config.cucm_host}...")
        
        self.transport = paramiko.Transport((self.config.cucm_host, self.config.cucm_port))
        self.transport.connect(
            username=self.config.cucm_username,
            password=self.config.cucm_password
        )
        self.sftp = paramiko.SFTPClient.from_transport(self.transport)
        
        logger.info("Connected to CUCM successfully")
    
    def disconnect(self):
        """Close SFTP connection"""
        if self.sftp:
            self.sftp.close()
        if self.transport:
            self.transport.close()
        logger.info("Disconnected from CUCM")
    
    def list_cdr_files(self, hours: int = 24) -> List[str]:
        """List CDR files from the last N hours"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        cdr_files = []
        
        try:
            files = self.sftp.listdir_attr(self.config.cucm_cdr_path)
            
            for file_attr in files:
                filename = file_attr.filename
                
                if filename.startswith('cdr_'):
                    mtime = datetime.fromtimestamp(file_attr.st_mtime)
                    if mtime >= cutoff_time:
                        cdr_files.append(filename)
            
            logger.info(f"Found {len(cdr_files)} CDR files from the last {hours} hours")
            
        except FileNotFoundError:
            logger.error(f"CDR path not found: {self.config.cucm_cdr_path}")
        except Exception as e:
            logger.error(f"Error listing CDR files: {e}")
        
        return sorted(cdr_files)
    
    def download_file(self, remote_filename: str) -> Optional[str]:
        """Download a single CDR file"""
        local_dir = Path(self.config.local_cdr_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        
        remote_path = f"{self.config.cucm_cdr_path}/{remote_filename}"
        local_path = local_dir / remote_filename
        
        try:
            self.sftp.get(remote_path, str(local_path))
            logger.debug(f"Downloaded: {remote_filename}")
            return str(local_path)
        except Exception as e:
            logger.error(f"Error downloading {remote_filename}: {e}")
            return None
    
    def download_cdr_files(self, hours: int = 24) -> List[str]:
        """Download all CDR files from the last N hours"""
        files_to_download = self.list_cdr_files(hours)
        downloaded_files = []
        
        for filename in files_to_download:
            local_path = self.download_file(filename)
            if local_path:
                downloaded_files.append(local_path)
        
        logger.info(f"Downloaded {len(downloaded_files)} CDR files")
        return downloaded_files


# =============================================================================
# CDR PARSING
# =============================================================================

class CDRParser:
    """Parses CUCM CDR flat files"""
    
    CDR_FIELDS = {
        0: 'cdrRecordType',
        1: 'globalCallID_callManagerId',
        2: 'globalCallID_callId',
        3: 'origLegCallIdentifier',
        4: 'dateTimeOrigination',
        5: 'origNodeId',
        6: 'origSpan',
        7: 'origIpAddr',
        8: 'callingPartyNumber',
        9: 'callingPartyUnicodeLoginUserID',
        10: 'origCause_location',
        11: 'origCause_value',
        12: 'origPrecedenceLevel',
        13: 'origMediaTransportAddress_IP',
        14: 'origMediaTransportAddress_Port',
        15: 'origMediaCap_payloadCapability',
        16: 'origMediaCap_maxFramesPerPacket',
        17: 'origMediaCap_g723BitRate',
        18: 'origVideoCap_Codec',
        19: 'origVideoCap_Bandwidth',
        20: 'origVideoCap_Resolution',
        21: 'origVideoTransportAddress_IP',
        22: 'origVideoTransportAddress_Port',
        23: 'origRSVPAudioStat',
        24: 'origRSVPVideoStat',
        25: 'destLegCallIdentifier',
        26: 'destNodeId',
        27: 'destSpan',
        28: 'destIpAddr',
        29: 'originalCalledPartyNumber',
        30: 'finalCalledPartyNumber',
        31: 'finalCalledPartyUnicodeLoginUserID',
        32: 'destCause_location',
        33: 'destCause_value',
        34: 'destPrecedenceLevel',
        35: 'destMediaTransportAddress_IP',
        36: 'destMediaTransportAddress_Port',
        37: 'destMediaCap_payloadCapability',
        38: 'destMediaCap_maxFramesPerPacket',
        39: 'destMediaCap_g723BitRate',
        40: 'destVideoCap_Codec',
        41: 'destVideoCap_Bandwidth',
        42: 'destVideoCap_Resolution',
        43: 'destVideoTransportAddress_IP',
        44: 'destVideoTransportAddress_Port',
        45: 'destRSVPAudioStat',
        46: 'destRSVPVideoStat',
        47: 'dateTimeConnect',
        48: 'dateTimeDisconnect',
        49: 'lastRedirectDn',
        50: 'pkid',
        51: 'originalCalledPartyNumberPartition',
        52: 'callingPartyNumberPartition',
        53: 'finalCalledPartyNumberPartition',
        54: 'lastRedirectDnPartition',
        55: 'duration',
        56: 'origDeviceName',
        57: 'destDeviceName',
        101: 'huntPilotDN',
        102: 'huntPilotPartition',
    }
    
    def __init__(self):
        pass
    
    def _safe_int(self, value: str, default: int = 0) -> int:
        """Safely convert string to integer"""
        try:
            return int(value) if value else default
        except (ValueError, TypeError):
            return default
    
    def _parse_timestamp(self, epoch_str: str) -> Optional[datetime]:
        """Convert epoch timestamp to datetime"""
        try:
            epoch = int(epoch_str) if epoch_str else 0
            if epoch > 0:
                return datetime.fromtimestamp(epoch)
            return None
        except (ValueError, TypeError):
            return None
    
    def _get_field(self, row: List[str], field_name: str) -> str:
        """Get a field value from a CDR row by field name"""
        for idx, name in self.CDR_FIELDS.items():
            if name == field_name and idx < len(row):
                return row[idx].strip() if row[idx] else ""
        return ""
    
    def _get_field_int(self, row: List[str], field_name: str) -> int:
        """Get an integer field value from a CDR row"""
        return self._safe_int(self._get_field(row, field_name))
    
    def parse_file(self, file_path: str) -> Tuple[List[CDRRecord], int]:
        """Parse a CDR file and return list of CDRRecord objects"""
        records = []
        total_rows = 0
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                reader = csv.reader(f)
                
                for row in reader:
                    total_rows += 1
                    
                    if not row or len(row) < 50:
                        continue
                    
                    record_type = self._safe_int(row[0] if row else "0")
                    if record_type != 1:
                        continue
                    
                    try:
                        record = CDRRecord(
                            cdr_record_type=record_type,
                            global_call_id=f"{self._get_field(row, 'globalCallID_callManagerId')}-{self._get_field(row, 'globalCallID_callId')}",
                            date_time_origination=self._parse_timestamp(self._get_field(row, 'dateTimeOrigination')),
                            date_time_connect=self._parse_timestamp(self._get_field(row, 'dateTimeConnect')),
                            date_time_disconnect=self._parse_timestamp(self._get_field(row, 'dateTimeDisconnect')),
                            calling_party_number=self._get_field(row, 'callingPartyNumber'),
                            original_called_party_number=self._get_field(row, 'originalCalledPartyNumber'),
                            final_called_party_number=self._get_field(row, 'finalCalledPartyNumber'),
                            last_redirect_dn=self._get_field(row, 'lastRedirectDn'),
                            orig_cause_value=self._get_field_int(row, 'origCause_value'),
                            dest_cause_value=self._get_field_int(row, 'destCause_value'),
                            duration=self._get_field_int(row, 'duration'),
                            orig_device_name=self._get_field(row, 'origDeviceName'),
                            dest_device_name=self._get_field(row, 'destDeviceName'),
                            orig_ip_addr=self._get_field(row, 'origIpAddr'),
                            dest_ip_addr=self._get_field(row, 'destIpAddr'),
                            calling_party_number_partition=self._get_field(row, 'callingPartyNumberPartition'),
                            original_called_party_number_partition=self._get_field(row, 'originalCalledPartyNumberPartition'),
                            final_called_party_number_partition=self._get_field(row, 'finalCalledPartyNumberPartition'),
                            hunt_pilot_dn=self._get_field(row, 'huntPilotDN'),
                            hunt_pilot_partition=self._get_field(row, 'huntPilotPartition'),
                        )
                        
                        if record.date_time_origination:
                            records.append(record)
                            
                    except Exception as e:
                        logger.debug(f"Error parsing row: {e}")
                        continue
                        
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
        
        return records, total_rows
    
    def get_file_hash(self, file_path: str) -> str:
        """Calculate MD5 hash of file for duplicate detection"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()


# =============================================================================
# REPORT GENERATION
# =============================================================================

class ReportGenerator:
    """Generates PDF and HTML reports for failed calls"""
    
    def __init__(self, config: Config):
        self.config = config
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()
    
    def _setup_custom_styles(self):
        """Setup custom paragraph styles"""
        self.styles.add(ParagraphStyle(
            name='CenterTitle',
            parent=self.styles['Title'],
            alignment=TA_CENTER,
            spaceAfter=30
        ))
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading2'],
            spaceBefore=20,
            spaceAfter=10,
            textColor=colors.HexColor('#1a5276')
        ))
        self.styles.add(ParagraphStyle(
            name='SubHeader',
            parent=self.styles['Heading3'],
            spaceBefore=15,
            spaceAfter=8
        ))
    
    def generate_pdf_report(self, stats: Dict, failed_calls: List[Dict], 
                           output_path: str) -> str:
        """Generate a PDF report"""
        doc = SimpleDocTemplate(
            output_path,
            pagesize=letter,
            rightMargin=0.5*inch,
            leftMargin=0.5*inch,
            topMargin=0.5*inch,
            bottomMargin=0.5*inch
        )
        
        story = []
        
        story.append(Paragraph(
            f"CUCM Failed Calls Report",
            self.styles['CenterTitle']
        ))
        story.append(Paragraph(
            f"{self.config.cluster_name}",
            self.styles['Normal']
        ))
        story.append(Paragraph(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            self.styles['Normal']
        ))
        story.append(Paragraph(
            f"Analysis Period: Last {stats['analysis_period_hours']} hours",
            self.styles['Normal']
        ))
        story.append(Spacer(1, 20))
        
        story.append(Paragraph("Executive Summary", self.styles['SectionHeader']))
        
        summary_data = [
            ['Total Failed Calls', 'Analysis Period', 'Report Generated'],
            [
                str(stats['total_failed_calls']),
                f"{stats['analysis_period_hours']} hours",
                datetime.now().strftime('%Y-%m-%d %H:%M')
            ]
        ]
        
        summary_table = Table(summary_data, colWidths=[2.5*inch, 2.5*inch, 2.5*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 14),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('TOPPADDING', (0, 1), (-1, -1), 15),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 15),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#ecf0f1')),
            ('GRID', (0, 0), (-1, -1), 1, colors.white),
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 20))
        
        if stats['by_cause']:
            story.append(Paragraph("Failures by Cause Code", self.styles['SectionHeader']))
            
            cause_data = [['Cause Code', 'Reason', 'Count', 'Percentage']]
            total = stats['total_failed_calls'] or 1
            
            for item in stats['by_cause'][:15]:
                pct = (item['count'] / total) * 100
                cause_data.append([
                    str(item['dest_cause_value']),
                    item['failure_reason'][:40] + '...' if len(item['failure_reason']) > 40 else item['failure_reason'],
                    str(item['count']),
                    f"{pct:.1f}%"
                ])
            
            cause_table = Table(cause_data, colWidths=[0.8*inch, 4*inch, 0.8*inch, 0.9*inch])
            cause_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('ALIGN', (0, 1), (0, -1), 'CENTER'),
                ('ALIGN', (2, 1), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('TOPPADDING', (0, 1), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
            ]))
            story.append(cause_table)
            story.append(Spacer(1, 15))
        
        if stats['top_devices']:
            story.append(Paragraph("Top Devices with Failures", self.styles['SectionHeader']))
            
            device_data = [['Device Name', 'Failed Calls']]
            for item in stats['top_devices'][:10]:
                device_data.append([item['orig_device_name'], str(item['count'])])
            
            device_table = Table(device_data, colWidths=[5*inch, 1.5*inch])
            device_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#34495e')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (1, 0), (1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('TOPPADDING', (0, 1), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
            ]))
            story.append(device_table)
        
        story.append(PageBreak())
        
        story.append(Paragraph("Recent Failed Calls Detail", self.styles['SectionHeader']))
        story.append(Paragraph(
            f"Showing most recent {min(50, len(failed_calls))} failed calls",
            self.styles['Normal']
        ))
        story.append(Spacer(1, 10))
        
        if failed_calls:
            detail_data = [['Time', 'From', 'To', 'Cause', 'Device']]
            
            for call in failed_calls[:50]:
                dt = call.get('date_time_origination', '')
                if isinstance(dt, str):
                    dt_str = dt[:16] if dt else 'N/A'
                else:
                    dt_str = dt.strftime('%m/%d %H:%M') if dt else 'N/A'
                
                detail_data.append([
                    dt_str,
                    (call.get('calling_party_number', '') or 'Unknown')[:15],
                    (call.get('original_called_party_number', '') or 'Unknown')[:15],
                    str(call.get('dest_cause_value', 0)),
                    (call.get('orig_device_name', '') or 'Unknown')[:20]
                ])
            
            detail_table = Table(detail_data, colWidths=[1.1*inch, 1.2*inch, 1.2*inch, 0.6*inch, 2.4*inch])
            detail_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('ALIGN', (3, 1), (3, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
                ('TOPPADDING', (0, 1), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 3),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bdc3c7')),
            ]))
            story.append(detail_table)
        
        doc.build(story)
        logger.info(f"PDF report generated: {output_path}")
        
        return output_path
    
    def generate_html_report(self, stats: Dict, failed_calls: List[Dict]) -> str:
        """Generate an HTML report for email body"""
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }}
                .container {{ max-width: 800px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                h1 {{ color: #2c3e50; text-align: center; margin-bottom: 5px; }}
                h2 {{ color: #34495e; border-bottom: 2px solid #3498db; padding-bottom: 10px; margin-top: 30px; }}
                .subtitle {{ text-align: center; color: #7f8c8d; margin-bottom: 30px; }}
                .summary-box {{ display: flex; justify-content: space-around; background-color: #ecf0f1; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                .stat {{ text-align: center; }}
                .stat-value {{ font-size: 36px; font-weight: bold; color: #c0392b; }}
                .stat-label {{ font-size: 14px; color: #7f8c8d; }}
                table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
                th {{ background-color: #34495e; color: white; padding: 12px 8px; text-align: left; font-size: 13px; }}
                td {{ padding: 10px 8px; border-bottom: 1px solid #ecf0f1; font-size: 12px; }}
                tr:nth-child(even) {{ background-color: #f8f9fa; }}
                .cause-code {{ font-weight: bold; color: #e74c3c; }}
                .footer {{ text-align: center; margin-top: 30px; padding-top: 20px; border-top: 1px solid #ecf0f1; color: #95a5a6; font-size: 12px; }}
                .alert {{ background-color: #fadbd8; border-left: 4px solid #e74c3c; padding: 15px; margin: 20px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>CUCM Failed Calls Report</h1>
                <p class="subtitle">{self.config.cluster_name} | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                
                <div class="summary-box">
                    <div class="stat">
                        <div class="stat-value">{stats['total_failed_calls']}</div>
                        <div class="stat-label">Total Failed Calls</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{stats['analysis_period_hours']}h</div>
                        <div class="stat-label">Analysis Period</div>
                    </div>
                </div>
        """
        
        if stats['total_failed_calls'] > 100:
            html += f"""
                <div class="alert">
                    High Failure Count Alert: {stats['total_failed_calls']} failed calls detected.
                </div>
            """
        
        if stats.get('by_cause'):
            html += """
                <h2>Failures by Cause Code</h2>
                <table>
                    <tr><th>Code</th><th>Reason</th><th>Count</th></tr>
            """
            for item in stats['by_cause'][:10]:
                html += f"""
                    <tr>
                        <td class="cause-code">{item['dest_cause_value']}</td>
                        <td>{item['failure_reason']}</td>
                        <td>{item['count']}</td>
                    </tr>
                """
            html += "</table>"
        
        if stats.get('top_devices'):
            html += """
                <h2>Top Devices with Failures</h2>
                <table>
                    <tr><th>Device Name</th><th>Failed Calls</th></tr>
            """
            for item in stats['top_devices'][:10]:
                html += f"""
                    <tr>
                        <td>{item['orig_device_name']}</td>
                        <td>{item['count']}</td>
                    </tr>
                """
            html += "</table>"
        
        html += f"""
                <div class="footer">
                    <p>Generated by CUCM CDR Failed Call Reporter</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html


# =============================================================================
# EMAIL SENDER
# =============================================================================

class EmailSender:
    """Handles email delivery of reports"""
    
    def __init__(self, config: Config):
        self.config = config
    
    def send_report(self, html_body: str, pdf_path: Optional[str] = None) -> bool:
        """Send the report via email"""
        
        if not self.config.email_to:
            logger.warning("No email recipients configured")
            return False
        
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = self.config.email_subject.format(
                date=datetime.now().strftime('%Y-%m-%d')
            )
            msg['From'] = self.config.email_from
            msg['To'] = ', '.join(self.config.email_to)
            
            msg.attach(MIMEText("Please view this email in an HTML-capable client.", 'plain'))
            msg.attach(MIMEText(html_body, 'html'))
            
            if pdf_path and os.path.exists(pdf_path):
                with open(pdf_path, 'rb') as f:
                    pdf_attachment = MIMEApplication(f.read(), _subtype='pdf')
                    pdf_attachment.add_header(
                        'Content-Disposition', 
                        'attachment', 
                        filename=os.path.basename(pdf_path)
                    )
                    msg.attach(pdf_attachment)
            
            if self.config.smtp_use_tls:
                server = smtplib.SMTP(self.config.smtp_server, self.config.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP(self.config.smtp_server, self.config.smtp_port)
            
            if self.config.smtp_username and self.config.smtp_password:
                server.login(self.config.smtp_username, self.config.smtp_password)
            
            server.sendmail(
                self.config.email_from,
                self.config.email_to,
                msg.as_string()
            )
            server.quit()
            
            logger.info(f"Report emailed to: {', '.join(self.config.email_to)}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False


# =============================================================================
# MAIN APPLICATION
# =============================================================================

class CDRReporter:
    """Main application class that orchestrates the CDR reporting process"""
    
    def __init__(self, config: Config):
        self.config = config
        self.db = CDRDatabase(config.database_path)
        self.parser = CDRParser()
        self.report_gen = ReportGenerator(config)
        self.email_sender = EmailSender(config)
    
    def fetch_and_process_cdr_files(self) -> Tuple[int, int]:
        """Fetch CDR files from CUCM and process them"""
        fetcher = CDRFetcher(self.config)
        total_records = 0
        total_failed = 0
        
        try:
            fetcher.connect()
            downloaded_files = fetcher.download_cdr_files(self.config.hours_to_analyze)
            
            for file_path in downloaded_files:
                file_hash = self.parser.get_file_hash(file_path)
                filename = os.path.basename(file_path)
                
                if self.db.is_file_processed(filename, file_hash):
                    logger.debug(f"Skipping already processed file: {filename}")
                    continue
                
                records, row_count = self.parser.parse_file(file_path)
                total_records += len(records)
                
                failed_count = 0
                for record in records:
                    if record.is_failed:
                        self.db.insert_failed_call(record, file_hash)
                        failed_count += 1
                
                total_failed += failed_count
                
                self.db.mark_file_processed(filename, file_hash, len(records), failed_count)
                logger.info(f"Processed {filename}: {len(records)} records, {failed_count} failed calls")
            
        except Exception as e:
            logger.error(f"Error during CDR fetch/process: {e}")
            raise
        finally:
            fetcher.disconnect()
        
        return total_records, total_failed
    
    def generate_and_send_report(self) -> bool:
        """Generate report and send via email"""
        
        stats = self.db.get_failure_statistics(self.config.hours_to_analyze)
        failed_calls = self.db.get_failed_calls(self.config.hours_to_analyze)
        
        html_report = self.report_gen.generate_html_report(stats, failed_calls)
        
        report_dir = Path(self.config.report_output_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        
        pdf_filename = f"cucm_failed_calls_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        pdf_path = str(report_dir / pdf_filename)
        
        self.report_gen.generate_pdf_report(stats, failed_calls, pdf_path)
        
        success = self.email_sender.send_report(html_report, pdf_path)
        
        return success
    
    def cleanup(self):
        """Perform cleanup operations"""
        self.db.cleanup_old_records(self.config.retention_days)
        
        report_dir = Path(self.config.report_output_dir)
        if report_dir.exists():
            cutoff = datetime.now() - timedelta(days=self.config.retention_days)
            for report_file in report_dir.glob('*.pdf'):
                if datetime.fromtimestamp(report_file.stat().st_mtime) < cutoff:
                    report_file.unlink()
                    logger.debug(f"Deleted old report: {report_file}")
    
    def run(self, skip_fetch: bool = False):
        """Run the complete reporting workflow"""
        logger.info("=" * 60)
        logger.info("Starting CUCM CDR Failed Call Reporter")
        logger.info("=" * 60)
        
        try:
            if not skip_fetch:
                logger.info("Fetching CDR files from CUCM...")
                total_records, total_failed = self.fetch_and_process_cdr_files()
                logger.info(f"Processed {total_records} records, found {total_failed} new failed calls")
            
            logger.info("Generating report...")
            self.generate_and_send_report()
            
            logger.info("Running cleanup...")
            self.cleanup()
            
            logger.info("Report generation complete!")
            
        except Exception as e:
            logger.error(f"Error during report generation: {e}")
            raise
        finally:
            self.db.close()


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def create_sample_config():
    """Create a sample configuration file"""
    config = Config(
        cucm_host="cucm-pub.example.com",
        cucm_port=22,
        cucm_username="cdr_user",
        cucm_password="your_password_here",
        cucm_cdr_path="/var/log/active/cm/cdr_repository/processed",
        local_cdr_dir="./cdr_files",
        database_path="./cdr_database.db",
        report_output_dir="./reports",
        hours_to_analyze=24,
        retention_days=7,
        smtp_server="smtp.example.com",
        smtp_port=587,
        smtp_username="reports@example.com",
        smtp_password="smtp_password_here",
        smtp_use_tls=True,
        email_from="cucm-reports@example.com",
        email_to=["admin@example.com", "voip-team@example.com"],
        email_subject="CUCM Failed Calls Report - {date}",
        cluster_name="Production CUCM Cluster"
    )
    config.to_file("config.json")
    print("Sample configuration file created: config.json")
    print("Please edit this file with your CUCM and email settings.")


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='CUCM CDR Failed Call Reporter',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Generate sample config:  python cucm_cdr_reporter.py --init
  Run with config file:    python cucm_cdr_reporter.py -c config.json
  Skip SFTP fetch:         python cucm_cdr_reporter.py -c config.json --skip-fetch
  Verbose output:          python cucm_cdr_reporter.py -c config.json -v
        """
    )
    
    parser.add_argument('-c', '--config', help='Path to configuration file')
    parser.add_argument('--init', action='store_true', help='Create sample configuration file')
    parser.add_argument('--skip-fetch', action='store_true', help='Skip SFTP fetch, use existing data')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    if args.init:
        create_sample_config()
        return
    
    if not args.config:
        parser.print_help()
        print("\nError: Configuration file required. Use --init to create one.")
        sys.exit(1)
    
    if not os.path.exists(args.config):
        print(f"Error: Configuration file not found: {args.config}")
        sys.exit(1)
    
    config = Config.from_file(args.config)
    reporter = CDRReporter(config)
    reporter.run(skip_fetch=args.skip_fetch)


if __name__ == '__main__':
    main()
