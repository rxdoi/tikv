#!/usr/bin/env python3
"""
Detect when all requests are done processing by monitoring the trace file.

Usage:
    python3 detect_completion.py [input_csv] [trace_file] [--timeout SECONDS] [--stable SECONDS] [--decision DECISION1,DECISION2]
    
Options:
    --decision: Filter by decision type(s). Valid values: scheduled, urgent-admit, check-delay, baseline-immediate
                Example: --decision scheduled,urgent-admit
                If not specified, counts all requests regardless of decision.
"""

import sys
import time
import os
import csv
from pathlib import Path
from collections import Counter
from typing import Optional

def count_expected_requests(input_csv: str) -> int:
    """Count expected requests from input CSV (excluding header)."""
    with open(input_csv, 'r') as f:
        reader = csv.DictReader(f)
        return sum(1 for _ in reader)

def count_unique_requests(trace_file: str, decision_filter: Optional[list] = None) -> int:
    """Count unique request IDs in trace file (excluding header).
    
    Args:
        trace_file: Path to trace CSV file
        decision_filter: Optional list of decision types to include (e.g., ['scheduled', 'urgent-admit']).
                        If None, counts all requests regardless of decision.
    """
    if not os.path.exists(trace_file):
        return 0
    request_ids = set()
    with open(trace_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if decision_filter is None or row['decision'] in decision_filter:
                request_ids.add(row['request_id'])
    return len(request_ids)

def get_file_stats(trace_file: str) -> Optional[dict]:
    """Get file size and modification time."""
    if not os.path.exists(trace_file):
        return None
    stat = os.stat(trace_file)
    return {
        'size': stat.st_size,
        'mtime': stat.st_mtime,
        'lines': sum(1 for _ in open(trace_file)) - 1  # Exclude header
    }

def monitor_stability(trace_file: str, stable_period: int = 30, timeout: int = 300):
    """Monitor file until it's stable for stable_period seconds."""
    print(f"Monitoring {trace_file} for stability...")
    print(f"Stable period: {stable_period}s, Timeout: {timeout}s\n")
    
    prev_size = 0
    stable_start = None
    start_time = time.time()
    check_interval = 5
    
    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout:
            print(f"\n⚠ Timeout reached ({timeout}s)")
            break
        
        stats = get_file_stats(trace_file)
        if stats is None:
            print(f"[{elapsed:.1f}s] File not found yet...")
            time.sleep(check_interval)
            continue
        
        current_size = stats['size']
        current_lines = stats['lines']
        age = time.time() - stats['mtime']
        
        if current_size == prev_size:
            if stable_start is None:
                stable_start = time.time()
            stable_duration = time.time() - stable_start
            if stable_duration >= stable_period:
                print(f"\n✓ File stable for {stable_duration:.1f}s")
                print(f"  Final size: {current_size:,} bytes")
                print(f"  Final lines: {current_lines:,}")
                print(f"  Last modified: {age:.1f}s ago")
                return True
        else:
            stable_start = None
        
        print(f"[{elapsed:.1f}s] Size: {current_size:,} bytes, "
              f"Lines: {current_lines:,}, "
              f"Age: {age:.1f}s, "
              f"Stable: {stable_duration if stable_start else 0:.1f}s")
        
        prev_size = current_size
        time.sleep(check_interval)
    
    return False

def verify_completion(input_csv: str, trace_file: str, decision_filter: Optional[list] = None):
    """Verify that all expected requests are in the trace.
    
    Args:
        input_csv: Path to input CSV file
        trace_file: Path to trace CSV file
        decision_filter: Optional list of decision types to filter by
    """
    print("\n" + "="*60)
    print("Verification")
    print("="*60)
    
    expected = count_expected_requests(input_csv)
    print(f"Expected requests: {expected:,}")
    
    if not os.path.exists(trace_file):
        print(f"⚠ Trace file not found: {trace_file}")
        return False
    
    # Count all requests (unfiltered)
    actual_all = count_unique_requests(trace_file, decision_filter=None)
    print(f"Actual unique requests (all decisions): {actual_all:,}")
    
    # Count filtered requests if filter specified
    if decision_filter:
        actual_filtered = count_unique_requests(trace_file, decision_filter=decision_filter)
        filter_str = ", ".join(decision_filter)
        print(f"Actual unique requests (filtered: {filter_str}): {actual_filtered:,}")
        actual = actual_filtered
    else:
        actual = actual_all
    
    if actual >= expected:
        print(f"✓ All expected requests processed")
        if actual > expected:
            print(f"  (Note: {actual - expected:,} extra requests found)")
        return True
    else:
        missing = expected - actual
        print(f"⚠ Missing {missing:,} requests ({100*missing/expected:.1f}%)")
        return False

def analyze_trace(trace_file: str):
    """Analyze trace file for insights."""
    if not os.path.exists(trace_file):
        return
    
    print("\n" + "="*60)
    print("Trace Analysis")
    print("="*60)
    
    decisions = Counter()
    priorities = Counter()
    
    with open(trace_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            decisions[row['decision']] += 1
            priorities[row['priority']] += 1
    
    print("\nDecisions:")
    for decision, count in decisions.most_common():
        print(f"  {decision}: {count:,}")
    
    print("\nPriorities:")
    for priority, count in priorities.most_common():
        print(f"  {priority}: {count:,}")

def main():
    input_csv = sys.argv[1] if len(sys.argv) > 1 else "requests_1million.csv"
    trace_file = sys.argv[2] if len(sys.argv) > 2 else "replay_trace_server.csv"
    
    timeout = 300
    stable_period = 30
    decision_filter = None
    
    # Parse optional args
    if '--timeout' in sys.argv:
        idx = sys.argv.index('--timeout')
        timeout = int(sys.argv[idx + 1])
    if '--stable' in sys.argv:
        idx = sys.argv.index('--stable')
        stable_period = int(sys.argv[idx + 1])
    if '--decision' in sys.argv:
        idx = sys.argv.index('--decision')
        decision_filter = sys.argv[idx + 1].split(',')
        decision_filter = [d.strip() for d in decision_filter]
    
    if not os.path.exists(input_csv):
        print(f"Error: Input CSV not found: {input_csv}")
        sys.exit(1)
    
    print("="*60)
    print("Request Completion Detector")
    print("="*60)
    print(f"Input CSV: {input_csv}")
    print(f"Trace file: {trace_file}")
    print()
    
    # Method 1: Monitor stability
    is_stable = monitor_stability(trace_file, stable_period, timeout)
    
    # Method 2: Verify counts
    is_complete = verify_completion(input_csv, trace_file, decision_filter=decision_filter)
    
    # Method 3: Analyze trace
    analyze_trace(trace_file)
    
    print("\n" + "="*60)
    if is_stable and is_complete:
        print("✓ All requests completed and trace is stable")
        sys.exit(0)
    else:
        print("⚠ Completion status uncertain")
        sys.exit(1)

if __name__ == "__main__":
    main()

