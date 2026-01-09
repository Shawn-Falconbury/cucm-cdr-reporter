#!/usr/bin/env python3
"""
Test Data Generator for CUCM CDR Reporter
==========================================
Generates sample CDR files to test the reporting functionality
without requiring a connection to CUCM.

Usage:
    python generate_test_data.py
    python cucm_cdr_reporter.py -c config.json --skip-fetch
"""

import os
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

# Sample data pools
DEVICE_NAMES = [
    "SEPB4A8B95D6F01", "SEPB4A8B95D6F02", "SEPB4A8B95D6F03",
    "SEPC4B2399F4A01", "SEPC4B2399F4A02", "SEPC4B2399F4A03",
    "CSFUSER001", "CSFUSER002", "CSFUSER003",
    "JABBERUSER01", "JABBERUSER02", "JABBERUSER03",
]

CALLING_NUMBERS = [
    "1001", "1002", "1003", "1004", "1005",
    "2001", "2002", "2003", "2004", "2005",
]

CALLED_NUMBERS = [
    "91555123456", "91555234567", "91555345678",
    "918001234567", "918009876543",
    "1100", "1200", "1300", "1400", "1500",
]

IP_ADDRESSES = [
    "10.1.1.100", "10.1.1.101", "10.1.1.102",
    "10.1.2.100", "10.1.2.101", "10.1.2.102",
]

# Cause codes with their relative frequencies
FAILED_CAUSE_CODES = {
    1: 10,   # Unallocated number
    3: 5,    # No route to destination
    17: 30,  # User busy
    18: 15,  # No user responding
    19: 25,  # No answer
    21: 10,  # Call rejected
    27: 8,   # Destination out of order
    28: 5,   # Invalid number format
    31: 15,  # Normal, unspecified
    34: 3,   # No circuit available
}

SUCCESS_CAUSE_CODES = [0, 16]


def weighted_choice(choices_dict):
    """Select a random key based on weighted values"""
    total = sum(choices_dict.values())
    r = random.uniform(0, total)
    cumulative = 0
    for choice, weight in choices_dict.items():
        cumulative += weight
        if r <= cumulative:
            return choice
    return list(choices_dict.keys())[0]


def generate_cdr_row(timestamp: datetime, is_failed: bool = False) -> list:
    """Generate a single CDR row"""
    
    calling_number = random.choice(CALLING_NUMBERS)
    called_number = random.choice(CALLED_NUMBERS)
    orig_device = random.choice(DEVICE_NAMES)
    dest_device = random.choice(DEVICE_NAMES)
    orig_ip = random.choice(IP_ADDRESSES)
    dest_ip = random.choice(IP_ADDRESSES)
    
    call_manager_id = random.randint(1, 2)
    call_id = random.randint(100000, 999999)
    
    epoch_origination = int(timestamp.timestamp())
    
    if is_failed:
        duration = 0
        dest_cause = weighted_choice(FAILED_CAUSE_CODES)
        orig_cause = 0
        epoch_connect = 0
        epoch_disconnect = epoch_origination + random.randint(1, 30)
    else:
        duration = random.randint(10, 600)
        dest_cause = random.choice(SUCCESS_CAUSE_CODES)
        orig_cause = random.choice(SUCCESS_CAUSE_CODES)
        epoch_connect = epoch_origination + random.randint(2, 10)
        epoch_disconnect = epoch_connect + duration
    
    row = [''] * 128
    
    row[0] = '1'
    row[1] = str(call_manager_id)
    row[2] = str(call_id)
    row[3] = str(random.randint(1000, 9999))
    row[4] = str(epoch_origination)
    row[5] = str(random.randint(1, 2))
    row[6] = '0'
    row[7] = orig_ip
    row[8] = calling_number
    row[11] = str(orig_cause)
    row[28] = dest_ip
    row[29] = called_number
    row[30] = called_number
    row[33] = str(dest_cause)
    row[47] = str(epoch_connect) if epoch_connect else ''
    row[48] = str(epoch_disconnect)
    row[55] = str(duration)
    row[56] = orig_device
    row[57] = dest_device
    
    return row


def generate_test_cdr_files(output_dir: str = './cdr_files', 
                           hours: int = 24,
                           calls_per_hour: int = 50,
                           failure_rate: float = 0.15):
    """Generate test CDR files"""
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    print(f"Generating {hours} hours of test CDR data...")
    print(f"  - Calls per hour: {calls_per_hour}")
    print(f"  - Failure rate: {failure_rate * 100:.0f}%")
    print(f"  - Output directory: {output_dir}")
    
    now = datetime.now()
    total_calls = 0
    total_failed = 0
    
    for hour_offset in range(hours, 0, -1):
        file_timestamp = now - timedelta(hours=hour_offset)
        filename = f"cdr_TestCluster_01_{file_timestamp.strftime('%Y%m%d%H%M%S')}_1"
        filepath = output_path / filename
        
        num_calls = calls_per_hour + random.randint(-10, 10)
        rows = []
        
        for i in range(num_calls):
            call_time = file_timestamp + timedelta(
                minutes=random.randint(0, 59),
                seconds=random.randint(0, 59)
            )
            
            is_failed = random.random() < failure_rate
            row = generate_cdr_row(call_time, is_failed)
            rows.append(row)
            
            total_calls += 1
            if is_failed:
                total_failed += 1
        
        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            for row in rows:
                writer.writerow(row)
        
        print(f"  Created: {filename} ({num_calls} calls)")
    
    print(f"\nGeneration complete!")
    print(f"  - Total calls: {total_calls}")
    print(f"  - Failed calls: {total_failed}")
    print(f"  - Success rate: {((total_calls - total_failed) / total_calls * 100):.1f}%")
    print(f"\nTo test the reporter:")
    print(f"  python cucm_cdr_reporter.py -c config.json --skip-fetch")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate test CDR data')
    parser.add_argument('--output', '-o', default='./cdr_files',
                       help='Output directory for CDR files')
    parser.add_argument('--hours', '-H', type=int, default=24,
                       help='Hours of data to generate')
    parser.add_argument('--calls', '-c', type=int, default=50,
                       help='Average calls per hour')
    parser.add_argument('--failure-rate', '-f', type=float, default=0.15,
                       help='Failure rate (0.0 - 1.0)')
    
    args = parser.parse_args()
    
    generate_test_cdr_files(
        output_dir=args.output,
        hours=args.hours,
        calls_per_hour=args.calls,
        failure_rate=args.failure_rate
    )


if __name__ == '__main__':
    main()
