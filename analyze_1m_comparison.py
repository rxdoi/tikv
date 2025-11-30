#!/usr/bin/env python3
"""
Compare 1M request results: Scheduler vs Baseline
"""

import csv
import statistics
from collections import defaultdict
import numpy as np

def load_trace(filename):
    """Load trace file and return list of records"""
    records = []
    with open(filename, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append({
                'request_id': row['request_id'],
                'priority': row['priority'],
                'arrival_ts': int(row['arrival_ts']),
                'deadline_ts': int(row['deadline_ts']),
                'delay_budget_ms': int(row['delay_budget_ms']),
                'scheduled_ts': int(row['scheduled_ts']),
                'scheduling_delay_ms': int(row['scheduling_delay_ms']),
                'available_threads': int(row['available_threads_at_schedule']),
                'required_threads': int(row['required_threads']),
                'decision': row['decision']
            })
    return records

def get_unique_requests(records):
    """Get unique requests (first occurrence of each request_id)"""
    seen = set()
    unique = []
    for r in records:
        if r['request_id'] not in seen:
            seen.add(r['request_id'])
            unique.append(r)
    return unique

def analyze_trace(records, name):
    """Analyze a trace and return statistics"""
    unique_requests = get_unique_requests(records)
    
    delays = [r['scheduling_delay_ms'] for r in unique_requests]
    
    stats = {
        'name': name,
        'total_events': len(records),
        'unique_requests': len(unique_requests),
        'scheduling_delays': delays,
        'by_priority': defaultdict(list),
        'by_decision': defaultdict(int),
        'deadline_violations': 0,
    }
    
    # Group by priority
    for r in unique_requests:
        stats['by_priority'][r['priority']].append(r['scheduling_delay_ms'])
        stats['by_decision'][r['decision']] += 1
    
    # Calculate deadline violations
    for r in unique_requests:
        if r['scheduling_delay_ms'] > r['delay_budget_ms']:
            stats['deadline_violations'] += 1
    
    # Calculate statistics
    if delays:
        stats['mean_delay'] = statistics.mean(delays)
        stats['median_delay'] = statistics.median(delays)
        stats['p95_delay'] = np.percentile(delays, 95)
        stats['p99_delay'] = np.percentile(delays, 99)
        stats['max_delay'] = max(delays)
        stats['zero_delay_pct'] = (sum(1 for d in delays if d == 0) / len(delays)) * 100
    else:
        stats['mean_delay'] = 0
        stats['median_delay'] = 0
        stats['p95_delay'] = 0
        stats['p99_delay'] = 0
        stats['max_delay'] = 0
        stats['zero_delay_pct'] = 0
    
    # Priority-specific stats
    for priority in ['HIGH', 'MEDIUM', 'LOW']:
        if priority in stats['by_priority']:
            pri_delays = stats['by_priority'][priority]
            stats[f'{priority}_mean'] = statistics.mean(pri_delays)
            stats[f'{priority}_zero_pct'] = (sum(1 for d in pri_delays if d == 0) / len(pri_delays)) * 100
        else:
            stats[f'{priority}_mean'] = 0
            stats[f'{priority}_zero_pct'] = 0
    
    return stats

def print_comparison(scheduler_stats, baseline_stats):
    """Print comparison report"""
    print("=" * 80)
    print("1 MILLION REQUEST COMPARISON: SCHEDULER vs BASELINE")
    print("=" * 80)
    
    print(f"\n📊 OVERVIEW")
    print(f"  Scheduler: {scheduler_stats['unique_requests']:,} requests, {scheduler_stats['total_events']:,} events")
    print(f"  Baseline:  {baseline_stats['unique_requests']:,} requests, {baseline_stats['total_events']:,} events")
    
    print(f"\n⏱️  SCHEDULING DELAY STATISTICS")
    print(f"  {'Metric':<20} {'Scheduler':<15} {'Baseline':<15} {'Improvement':<15}")
    print(f"  {'-'*20} {'-'*15} {'-'*15} {'-'*15}")
    
    metrics = [
        ('Mean Delay (ms)', 'mean_delay'),
        ('Median Delay (ms)', 'median_delay'),
        ('P95 Delay (ms)', 'p95_delay'),
        ('P99 Delay (ms)', 'p99_delay'),
        ('Max Delay (ms)', 'max_delay'),
        ('Zero-Delay %', 'zero_delay_pct'),
    ]
    
    for label, key in metrics:
        sched_val = scheduler_stats[key]
        base_val = baseline_stats[key]
        if key == 'zero_delay_pct':
            improvement = sched_val - base_val  # Percentage point difference
            print(f"  {label:<20} {sched_val:<15.2f} {base_val:<15.2f} {improvement:>+6.2f}pp")
        elif base_val > 0:
            improvement = ((base_val - sched_val) / base_val) * 100
            print(f"  {label:<20} {sched_val:<15.2f} {base_val:<15.2f} {improvement:>6.1f}%")
        else:
            print(f"  {label:<20} {sched_val:<15.2f} {base_val:<15.2f} {'N/A':>15}")
    
    print(f"\n🎯 PRIORITY-BASED PERFORMANCE")
    print(f"  {'Priority':<10} {'Scheduler Mean':<15} {'Baseline Mean':<15} {'Benefit':<15} {'Zero-Delay Gap':<15}")
    print(f"  {'-'*10} {'-'*15} {'-'*15} {'-'*15} {'-'*15}")
    
    for priority in ['HIGH', 'MEDIUM', 'LOW']:
        sched_mean = scheduler_stats.get(f'{priority}_mean', 0)
        base_mean = baseline_stats.get(f'{priority}_mean', 0)
        sched_zero = scheduler_stats.get(f'{priority}_zero_pct', 0)
        base_zero = baseline_stats.get(f'{priority}_zero_pct', 0)
        
        if base_mean > 0:
            benefit = ((base_mean - sched_mean) / base_mean) * 100
            zero_gap = sched_zero - base_zero
            print(f"  {priority:<10} {sched_mean:<15.2f} {base_mean:<15.2f} {benefit:>6.1f}% {zero_gap:>+6.2f}pp")
        else:
            print(f"  {priority:<10} {sched_mean:<15.2f} {base_mean:<15.2f} {'N/A':>15} {zero_gap:>+6.2f}pp")
    
    print(f"\n🚦 DECISION BREAKDOWN (Scheduler)")
    for decision, count in sorted(scheduler_stats['by_decision'].items()):
        pct = (count / scheduler_stats['total_events']) * 100
        print(f"  {decision:<20} {count:>8,} ({pct:>5.1f}%)")
    
    print(f"\n⚠️  DEADLINE VIOLATIONS")
    sched_violations = scheduler_stats['deadline_violations']
    base_violations = baseline_stats['deadline_violations']
    sched_pct = (sched_violations / scheduler_stats['unique_requests']) * 100 if scheduler_stats['unique_requests'] > 0 else 0
    base_pct = (base_violations / baseline_stats['unique_requests']) * 100 if baseline_stats['unique_requests'] > 0 else 0
    
    print(f"  Scheduler: {sched_violations:,} violations ({sched_pct:.3f}%)")
    print(f"  Baseline:  {base_violations:,} violations ({base_pct:.3f}%)")
    if base_violations > 0:
        reduction = ((base_violations - sched_violations) / base_violations) * 100
        print(f"  Reduction:  {reduction:.1f}% ({base_violations - sched_violations:,} fewer violations)")
    
    # Priority ordering analysis
    print(f"\n🔬 PRIORITY ORDERING EFFECTIVENESS")
    print(f"  (How often HIGH priority is scheduled before LOW priority)")
    # This would require more complex analysis of arrival/scheduled times
    print(f"  (See detailed analysis in trace files)")

def main():
    import sys
    
    scheduler_file = 'trace_actual_1m.csv'
    baseline_file = 'replay_trace_server_1m.csv'
    
    if len(sys.argv) > 1:
        scheduler_file = sys.argv[1]
    if len(sys.argv) > 2:
        baseline_file = sys.argv[2]
    
    print("Loading traces...")
    try:
        scheduler_records = load_trace(scheduler_file)
        baseline_records = load_trace(baseline_file)
    except FileNotFoundError as e:
        print(f"❌ Error: {e}")
        print(f"\nUsage: python3 {sys.argv[0]} [scheduler_trace.csv] [baseline_trace.csv]")
        print(f"\nExpected files:")
        print(f"  Scheduler: {scheduler_file}")
        print(f"  Baseline:  {baseline_file}")
        return
    
    print(f"  Scheduler: {len(scheduler_records):,} events")
    print(f"  Baseline:  {len(baseline_records):,} events")
    
    print("\nAnalyzing...")
    scheduler_stats = analyze_trace(scheduler_records, 'Agentic Scheduler')
    baseline_stats = analyze_trace(baseline_records, 'Baseline')
    
    print_comparison(scheduler_stats, baseline_stats)
    
    print("\n" + "=" * 80)
    print("Analysis complete!")
    print("=" * 80)

if __name__ == '__main__':
    main()

