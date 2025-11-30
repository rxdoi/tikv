#!/usr/bin/env python3
"""
Agentic Scheduler vs Baseline Comparison Analysis
For class project presentation
"""

import csv
import statistics
from collections import defaultdict
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

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
    
    stats = {
        'name': name,
        'total_events': len(records),
        'unique_requests': len(unique_requests),
        'scheduling_delays': [r['scheduling_delay_ms'] for r in unique_requests],
        'by_priority': defaultdict(list),
        'by_decision': defaultdict(int),
        'deadline_violations': 0,
        'utilization': []
    }
    
    # Group by priority
    for r in unique_requests:
        stats['by_priority'][r['priority']].append(r['scheduling_delay_ms'])
        stats['by_decision'][r['decision']] += 1
    
    # Calculate deadline violations (scheduling_delay > delay_budget)
    for r in unique_requests:
        if r['scheduling_delay_ms'] > r['delay_budget_ms']:
            stats['deadline_violations'] += 1
    
    # Calculate statistics
    if stats['scheduling_delays']:
        stats['mean_delay'] = statistics.mean(stats['scheduling_delays'])
        stats['median_delay'] = statistics.median(stats['scheduling_delays'])
        stats['p95_delay'] = np.percentile(stats['scheduling_delays'], 95)
        stats['p99_delay'] = np.percentile(stats['scheduling_delays'], 99)
        stats['max_delay'] = max(stats['scheduling_delays'])
    else:
        stats['mean_delay'] = 0
        stats['median_delay'] = 0
        stats['p95_delay'] = 0
        stats['p99_delay'] = 0
        stats['max_delay'] = 0
    
    # Priority-specific stats
    for priority in ['HIGH', 'MEDIUM', 'LOW']:
        if priority in stats['by_priority']:
            delays = stats['by_priority'][priority]
            stats[f'{priority}_mean'] = statistics.mean(delays)
            stats[f'{priority}_median'] = statistics.median(delays)
        else:
            stats[f'{priority}_mean'] = 0
            stats[f'{priority}_median'] = 0
    
    return stats

def print_comparison(scheduler_stats, baseline_stats):
    """Print comparison report"""
    print("=" * 80)
    print("AGENTIC SCHEDULER vs BASELINE COMPARISON")
    print("=" * 80)
    
    print(f"\n📊 OVERVIEW")
    print(f"  Scheduler: {scheduler_stats['unique_requests']} requests, {scheduler_stats['total_events']} events")
    print(f"  Baseline:  {baseline_stats['unique_requests']} requests, {baseline_stats['total_events']} events")
    
    print(f"\n⏱️  SCHEDULING DELAY STATISTICS")
    print(f"  {'Metric':<20} {'Scheduler':<15} {'Baseline':<15} {'Improvement':<15}")
    print(f"  {'-'*20} {'-'*15} {'-'*15} {'-'*15}")
    
    metrics = [
        ('Mean Delay (ms)', 'mean_delay'),
        ('Median Delay (ms)', 'median_delay'),
        ('P95 Delay (ms)', 'p95_delay'),
        ('P99 Delay (ms)', 'p99_delay'),
        ('Max Delay (ms)', 'max_delay'),
    ]
    
    for label, key in metrics:
        sched_val = scheduler_stats[key]
        base_val = baseline_stats[key]
        if base_val > 0:
            improvement = ((base_val - sched_val) / base_val) * 100
            print(f"  {label:<20} {sched_val:<15.2f} {base_val:<15.2f} {improvement:>6.1f}%")
        else:
            print(f"  {label:<20} {sched_val:<15.2f} {base_val:<15.2f} {'N/A':>15}")
    
    print(f"\n🎯 PRIORITY-BASED PERFORMANCE")
    print(f"  {'Priority':<10} {'Scheduler Mean':<15} {'Baseline Mean':<15} {'Benefit':<15}")
    print(f"  {'-'*10} {'-'*15} {'-'*15} {'-'*15}")
    
    for priority in ['HIGH', 'MEDIUM', 'LOW']:
        sched_mean = scheduler_stats.get(f'{priority}_mean', 0)
        base_mean = baseline_stats.get(f'{priority}_mean', 0)
        if base_mean > 0:
            benefit = ((base_mean - sched_mean) / base_mean) * 100
            print(f"  {priority:<10} {sched_mean:<15.2f} {base_mean:<15.2f} {benefit:>6.1f}%")
        else:
            print(f"  {priority:<10} {sched_mean:<15.2f} {base_mean:<15.2f} {'N/A':>15}")
    
    print(f"\n🚦 DECISION BREAKDOWN (Scheduler)")
    for decision, count in sorted(scheduler_stats['by_decision'].items()):
        pct = (count / scheduler_stats['total_events']) * 100
        print(f"  {decision:<20} {count:>6} ({pct:>5.1f}%)")
    
    print(f"\n⚠️  DEADLINE VIOLATIONS")
    sched_violations = scheduler_stats['deadline_violations']
    base_violations = baseline_stats['deadline_violations']
    sched_pct = (sched_violations / scheduler_stats['unique_requests']) * 100 if scheduler_stats['unique_requests'] > 0 else 0
    base_pct = (base_violations / baseline_stats['unique_requests']) * 100 if baseline_stats['unique_requests'] > 0 else 0
    
    print(f"  Scheduler: {sched_violations} violations ({sched_pct:.2f}%)")
    print(f"  Baseline:  {base_violations} violations ({base_pct:.2f}%)")
    if base_violations > 0:
        reduction = ((base_violations - sched_violations) / base_violations) * 100
        print(f"  Reduction:  {reduction:.1f}%")

def create_visualizations(scheduler_stats, baseline_stats):
    """Create comparison visualizations"""
    
    # 1. Delay Distribution Comparison
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle('Agentic Scheduler vs Baseline Performance Comparison', fontsize=16, fontweight='bold')
    
    # 1.1 CDF of Scheduling Delays
    ax1 = axes[0, 0]
    sched_delays = sorted(scheduler_stats['scheduling_delays'])
    base_delays = sorted(baseline_stats['scheduling_delays'])
    
    if sched_delays:
        ax1.plot(sched_delays, np.linspace(0, 100, len(sched_delays)), 
                label='Agentic Scheduler', linewidth=2, color='#2E86AB')
    if base_delays:
        ax1.plot(base_delays, np.linspace(0, 100, len(base_delays)), 
                label='Baseline (FCFS)', linewidth=2, color='#A23B72', linestyle='--')
    
    ax1.set_xlabel('Scheduling Delay (ms)', fontsize=11)
    ax1.set_ylabel('Cumulative Percentage (%)', fontsize=11)
    ax1.set_title('CDF: Scheduling Delay Distribution', fontsize=12, fontweight='bold')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(left=0)
    
    # 1.2 Mean Delay by Priority
    ax2 = axes[0, 1]
    priorities = ['HIGH', 'MEDIUM', 'LOW']
    sched_means = [scheduler_stats.get(f'{p}_mean', 0) for p in priorities]
    base_means = [baseline_stats.get(f'{p}_mean', 0) for p in priorities]
    
    x = np.arange(len(priorities))
    width = 0.35
    
    ax2.bar(x - width/2, sched_means, width, label='Agentic Scheduler', color='#2E86AB', alpha=0.8)
    ax2.bar(x + width/2, base_means, width, label='Baseline (FCFS)', color='#A23B72', alpha=0.8)
    
    ax2.set_xlabel('Priority', fontsize=11)
    ax2.set_ylabel('Mean Scheduling Delay (ms)', fontsize=11)
    ax2.set_title('Mean Delay by Priority', fontsize=12, fontweight='bold')
    ax2.set_xticks(x)
    ax2.set_xticklabels(priorities)
    ax2.legend()
    ax2.grid(True, alpha=0.3, axis='y')
    
    # 1.3 Percentile Comparison
    ax3 = axes[1, 0]
    percentiles = ['Mean', 'Median', 'P95', 'P99']
    sched_vals = [
        scheduler_stats['mean_delay'],
        scheduler_stats['median_delay'],
        scheduler_stats['p95_delay'],
        scheduler_stats['p99_delay']
    ]
    base_vals = [
        baseline_stats['mean_delay'],
        baseline_stats['median_delay'],
        baseline_stats['p95_delay'],
        baseline_stats['p99_delay']
    ]
    
    x = np.arange(len(percentiles))
    ax3.bar(x - width/2, sched_vals, width, label='Agentic Scheduler', color='#2E86AB', alpha=0.8)
    ax3.bar(x + width/2, base_vals, width, label='Baseline (FCFS)', color='#A23B72', alpha=0.8)
    
    ax3.set_xlabel('Percentile', fontsize=11)
    ax3.set_ylabel('Delay (ms)', fontsize=11)
    ax3.set_title('Delay Percentiles Comparison', fontsize=12, fontweight='bold')
    ax3.set_xticks(x)
    ax3.set_xticklabels(percentiles)
    ax3.legend()
    ax3.grid(True, alpha=0.3, axis='y')
    
    # 1.4 Decision Breakdown (Scheduler only)
    ax4 = axes[1, 1]
    decisions = list(scheduler_stats['by_decision'].keys())
    counts = list(scheduler_stats['by_decision'].values())
    colors = {'scheduled': '#06A77D', 'urgent-admit': '#F18F01', 'check-delay': '#C73E1D'}
    bar_colors = [colors.get(d, '#6C757D') for d in decisions]
    
    ax4.bar(decisions, counts, color=bar_colors, alpha=0.8)
    ax4.set_xlabel('Decision Type', fontsize=11)
    ax4.set_ylabel('Count', fontsize=11)
    ax4.set_title('Scheduler Decision Breakdown', fontsize=12, fontweight='bold')
    ax4.tick_params(axis='x', rotation=45)
    ax4.grid(True, alpha=0.3, axis='y')
    
    plt.tight_layout()
    plt.savefig('scheduler_comparison.png', dpi=300, bbox_inches='tight')
    print(f"\n✅ Saved visualization: scheduler_comparison.png")
    
    # 2. Timeline visualization (sample)
    fig2, ax = plt.subplots(figsize=(14, 6))
    
    # Sample first 100 requests for timeline
    sched_unique = get_unique_requests(load_trace('trace_actual.csv'))[:100]
    base_unique = get_unique_requests(load_trace('replay_trace_server.csv'))[:100]
    
    # Normalize arrival times to start at 0
    if sched_unique and base_unique:
        min_time = min(sched_unique[0]['arrival_ts'], base_unique[0]['arrival_ts'])
        
        sched_times = [(r['arrival_ts'] - min_time) / 1000 for r in sched_unique]
        sched_delays = [r['scheduling_delay_ms'] for r in sched_unique]
        
        base_times = [(r['arrival_ts'] - min_time) / 1000 for r in base_unique]
        base_delays = [r['scheduling_delay_ms'] for r in base_unique]
        
        ax.scatter(sched_times, sched_delays, alpha=0.6, s=20, 
                  label='Agentic Scheduler', color='#2E86AB', marker='o')
        ax.scatter(base_times, base_delays, alpha=0.6, s=20, 
                  label='Baseline (FCFS)', color='#A23B72', marker='x')
        
        ax.set_xlabel('Time (seconds)', fontsize=11)
        ax.set_ylabel('Scheduling Delay (ms)', fontsize=11)
        ax.set_title('Scheduling Delay Over Time (First 100 Requests)', fontsize=12, fontweight='bold')
        ax.legend()
        ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('scheduler_timeline.png', dpi=300, bbox_inches='tight')
    print(f"✅ Saved visualization: scheduler_timeline.png")

def main():
    print("Loading traces...")
    scheduler_records = load_trace('trace_actual.csv')
    baseline_records = load_trace('replay_trace_server.csv')
    
    print(f"  Scheduler: {len(scheduler_records)} events")
    print(f"  Baseline:  {len(baseline_records)} events")
    
    print("\nAnalyzing...")
    scheduler_stats = analyze_trace(scheduler_records, 'Agentic Scheduler')
    baseline_stats = analyze_trace(baseline_records, 'Baseline')
    
    print_comparison(scheduler_stats, baseline_stats)
    
    print("\nGenerating visualizations...")
    try:
        create_visualizations(scheduler_stats, baseline_stats)
    except ImportError:
        print("⚠️  matplotlib not available. Skipping visualizations.")
        print("   Install with: pip install matplotlib numpy")
    
    print("\n" + "=" * 80)
    print("Analysis complete!")
    print("=" * 80)

if __name__ == '__main__':
    main()

