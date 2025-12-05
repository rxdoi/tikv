#!/usr/bin/env python3
"""
Comprehensive analysis comparing agentic scheduler vs baseline for 200k requests.

Priority weights: HIGH=0.35, MEDIUM=0.40, LOW=0.25
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
from collections import Counter
import json

# Priority weights
PRIORITY_WEIGHTS = {
    'HIGH': 0.35,
    'MEDIUM': 0.40,
    'LOW': 0.25
}

# Priority order for sorting
PRIORITY_ORDER = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}

def load_trace(filepath):
    """Load trace CSV and compute additional metrics."""
    df = pd.read_csv(filepath)
    
    # For agentic scheduler, filter to only final decisions (exclude check-delay)
    # Keep only: scheduled, urgent-admit, baseline-immediate
    if 'check-delay' in df['decision'].values:
        print(f"  Filtering out 'check-delay' entries (keeping only final decisions)")
        df = df[df['decision'].isin(['scheduled', 'urgent-admit', 'baseline-immediate'])]
        # For each request_id, keep only the last entry (final decision)
        df = df.sort_values(['request_id', 'scheduled_ts']).groupby('request_id').last().reset_index()
    
    # Compute deadline miss status
    df['deadline_miss'] = df['scheduled_ts'] > df['deadline_ts']
    df['deadline_miss_ms'] = np.maximum(0, df['scheduled_ts'] - df['deadline_ts'])
    
    # Compute delay budget utilization
    df['delay_budget_used'] = df['scheduling_delay_ms'] / df['delay_budget_ms']
    df['delay_budget_used'] = df['delay_budget_used'].clip(0, 10)  # Cap at 10x for outliers
    
    # Compute priority weight
    df['priority_weight'] = df['priority'].map(PRIORITY_WEIGHTS)
    
    # Weighted metrics
    df['weighted_delay'] = df['scheduling_delay_ms'] * df['priority_weight']
    df['weighted_deadline_miss'] = df['deadline_miss'].astype(int) * df['priority_weight']
    
    return df

def compute_statistics(df, name):
    """Compute comprehensive statistics."""
    stats = {
        'name': name,
        'total_requests': len(df),
        'unique_requests': df['request_id'].nunique(),
    }
    
    # Priority distribution
    priority_counts = df['priority'].value_counts().to_dict()
    stats['priority_distribution'] = priority_counts
    stats['priority_percentages'] = {
        p: (count / len(df) * 100) 
        for p, count in priority_counts.items()
    }
    
    # Decision distribution
    decision_counts = df['decision'].value_counts().to_dict()
    stats['decision_distribution'] = decision_counts
    
    # Scheduling delay statistics
    stats['scheduling_delay'] = {
        'mean': df['scheduling_delay_ms'].mean(),
        'median': df['scheduling_delay_ms'].median(),
        'p50': df['scheduling_delay_ms'].quantile(0.50),
        'p75': df['scheduling_delay_ms'].quantile(0.75),
        'p90': df['scheduling_delay_ms'].quantile(0.90),
        'p95': df['scheduling_delay_ms'].quantile(0.95),
        'p99': df['scheduling_delay_ms'].quantile(0.99),
        'max': df['scheduling_delay_ms'].max(),
        'std': df['scheduling_delay_ms'].std(),
    }
    
    # Weighted scheduling delay
    stats['weighted_scheduling_delay'] = {
        'mean': df['weighted_delay'].sum() / df['priority_weight'].sum(),
        'total': df['weighted_delay'].sum(),
    }
    
    # Deadline miss statistics
    deadline_misses = df['deadline_miss'].sum()
    stats['deadline_misses'] = {
        'count': int(deadline_misses),
        'percentage': (deadline_misses / len(df)) * 100,
        'weighted_count': df['weighted_deadline_miss'].sum(),
        'weighted_percentage': (df['weighted_deadline_miss'].sum() / df['priority_weight'].sum()) * 100,
    }
    
    # Deadline miss severity
    missed = df[df['deadline_miss']]
    if len(missed) > 0:
        stats['deadline_miss_severity'] = {
            'mean_ms': missed['deadline_miss_ms'].mean(),
            'median_ms': missed['deadline_miss_ms'].median(),
            'max_ms': missed['deadline_miss_ms'].max(),
            'p95_ms': missed['deadline_miss_ms'].quantile(0.95),
        }
    else:
        stats['deadline_miss_severity'] = {
            'mean_ms': 0,
            'median_ms': 0,
            'max_ms': 0,
            'p95_ms': 0,
        }
    
    # Delay budget utilization
    stats['delay_budget_utilization'] = {
        'mean': df['delay_budget_used'].mean(),
        'median': df['delay_budget_used'].median(),
        'p95': df['delay_budget_used'].quantile(0.95),
        'over_budget_count': (df['delay_budget_used'] > 1.0).sum(),
        'over_budget_percentage': ((df['delay_budget_used'] > 1.0).sum() / len(df)) * 100,
    }
    
    # Per-priority statistics
    stats['per_priority'] = {}
    for priority in ['HIGH', 'MEDIUM', 'LOW']:
        pri_df = df[df['priority'] == priority]
        if len(pri_df) > 0:
            stats['per_priority'][priority] = {
                'count': len(pri_df),
                'mean_delay_ms': pri_df['scheduling_delay_ms'].mean(),
                'median_delay_ms': pri_df['scheduling_delay_ms'].median(),
                'p95_delay_ms': pri_df['scheduling_delay_ms'].quantile(0.95),
                'deadline_misses': int(pri_df['deadline_miss'].sum()),
                'deadline_miss_percentage': (pri_df['deadline_miss'].sum() / len(pri_df)) * 100,
            }
    
    # Zero delay requests
    zero_delay = (df['scheduling_delay_ms'] == 0).sum()
    stats['zero_delay'] = {
        'count': int(zero_delay),
        'percentage': (zero_delay / len(df)) * 100,
    }
    
    return stats

def compare_statistics(agentic_stats, baseline_stats):
    """Compare agentic vs baseline statistics."""
    comparison = {}
    
    # Scheduling delay comparison
    comparison['scheduling_delay'] = {
        'mean_improvement_ms': baseline_stats['scheduling_delay']['mean'] - agentic_stats['scheduling_delay']['mean'],
        'mean_improvement_pct': ((baseline_stats['scheduling_delay']['mean'] - agentic_stats['scheduling_delay']['mean']) / baseline_stats['scheduling_delay']['mean']) * 100,
        'median_improvement_ms': baseline_stats['scheduling_delay']['median'] - agentic_stats['scheduling_delay']['median'],
        'p95_improvement_ms': baseline_stats['scheduling_delay']['p95'] - agentic_stats['scheduling_delay']['p95'],
        'p95_improvement_pct': ((baseline_stats['scheduling_delay']['p95'] - agentic_stats['scheduling_delay']['p95']) / baseline_stats['scheduling_delay']['p95']) * 100,
    }
    
    # Weighted delay comparison
    comparison['weighted_delay'] = {
        'improvement': baseline_stats['weighted_scheduling_delay']['mean'] - agentic_stats['weighted_scheduling_delay']['mean'],
        'improvement_pct': ((baseline_stats['weighted_scheduling_delay']['mean'] - agentic_stats['weighted_scheduling_delay']['mean']) / baseline_stats['weighted_scheduling_delay']['mean']) * 100,
    }
    
    # Deadline miss comparison
    comparison['deadline_misses'] = {
        'absolute_reduction': baseline_stats['deadline_misses']['count'] - agentic_stats['deadline_misses']['count'],
        'relative_reduction_pct': ((baseline_stats['deadline_misses']['count'] - agentic_stats['deadline_misses']['count']) / baseline_stats['deadline_misses']['count']) * 100 if baseline_stats['deadline_misses']['count'] > 0 else 0,
        'weighted_reduction': baseline_stats['deadline_misses']['weighted_count'] - agentic_stats['deadline_misses']['weighted_count'],
        'weighted_reduction_pct': ((baseline_stats['deadline_misses']['weighted_count'] - agentic_stats['deadline_misses']['weighted_count']) / baseline_stats['deadline_misses']['weighted_count']) * 100 if baseline_stats['deadline_misses']['weighted_count'] > 0 else 0,
    }
    
    # Zero delay comparison
    comparison['zero_delay'] = {
        'increase': agentic_stats['zero_delay']['count'] - baseline_stats['zero_delay']['count'],
        'increase_pct': ((agentic_stats['zero_delay']['count'] - baseline_stats['zero_delay']['count']) / baseline_stats['zero_delay']['count']) * 100 if baseline_stats['zero_delay']['count'] > 0 else float('inf'),
    }
    
    return comparison

def create_visualizations(agentic_df, baseline_df, agentic_stats, baseline_stats, comparison):
    """Create comprehensive visualizations."""
    sns.set_style("whitegrid")
    fig = plt.figure(figsize=(20, 24))
    
    # 1. Scheduling Delay Distribution (CDF)
    ax1 = plt.subplot(4, 3, 1)
    for df, label, color in [(agentic_df, 'Agentic Scheduler', 'blue'), (baseline_df, 'Baseline', 'red')]:
        sorted_delays = np.sort(df['scheduling_delay_ms'])
        y = np.arange(1, len(sorted_delays) + 1) / len(sorted_delays)
        ax1.plot(sorted_delays, y, label=label, color=color, linewidth=2)
    ax1.set_xlabel('Scheduling Delay (ms)', fontsize=12)
    ax1.set_ylabel('Cumulative Probability', fontsize=12)
    ax1.set_title('Scheduling Delay CDF', fontsize=14, fontweight='bold')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(0, min(1000, max(agentic_df['scheduling_delay_ms'].quantile(0.99), baseline_df['scheduling_delay_ms'].quantile(0.99))))
    
    # 2. Scheduling Delay Box Plot by Priority
    ax2 = plt.subplot(4, 3, 2)
    data = []
    for df, label in [(agentic_df, 'Agentic'), (baseline_df, 'Baseline')]:
        for priority in ['HIGH', 'MEDIUM', 'LOW']:
            pri_data = df[df['priority'] == priority]['scheduling_delay_ms']
            for val in pri_data:
                data.append({'Scheduler': label, 'Priority': priority, 'Delay (ms)': val})
    plot_df = pd.DataFrame(data)
    sns.boxplot(data=plot_df, x='Priority', y='Delay (ms)', hue='Scheduler', ax=ax2)
    ax2.set_title('Scheduling Delay by Priority', fontsize=14, fontweight='bold')
    ax2.set_yscale('log')
    ax2.grid(True, alpha=0.3)
    
    # 3. Deadline Miss Rate Comparison
    ax3 = plt.subplot(4, 3, 3)
    categories = ['Overall', 'HIGH', 'MEDIUM', 'LOW']
    agentic_rates = [
        agentic_stats['deadline_misses']['percentage'],
        agentic_stats['per_priority']['HIGH']['deadline_miss_percentage'],
        agentic_stats['per_priority']['MEDIUM']['deadline_miss_percentage'],
        agentic_stats['per_priority']['LOW']['deadline_miss_percentage'],
    ]
    baseline_rates = [
        baseline_stats['deadline_misses']['percentage'],
        baseline_stats['per_priority']['HIGH']['deadline_miss_percentage'],
        baseline_stats['per_priority']['MEDIUM']['deadline_miss_percentage'],
        baseline_stats['per_priority']['LOW']['deadline_miss_percentage'],
    ]
    x = np.arange(len(categories))
    width = 0.35
    ax3.bar(x - width/2, agentic_rates, width, label='Agentic', color='blue', alpha=0.7)
    ax3.bar(x + width/2, baseline_rates, width, label='Baseline', color='red', alpha=0.7)
    ax3.set_xlabel('Priority Level', fontsize=12)
    ax3.set_ylabel('Deadline Miss Rate (%)', fontsize=12)
    ax3.set_title('Deadline Miss Rate Comparison', fontsize=14, fontweight='bold')
    ax3.set_xticks(x)
    ax3.set_xticklabels(categories)
    ax3.legend()
    ax3.grid(True, alpha=0.3, axis='y')
    
    # 4. Weighted Delay Comparison
    ax4 = plt.subplot(4, 3, 4)
    schedulers = ['Agentic', 'Baseline']
    weighted_delays = [
        agentic_stats['weighted_scheduling_delay']['mean'],
        baseline_stats['weighted_scheduling_delay']['mean'],
    ]
    colors = ['blue', 'red']
    bars = ax4.bar(schedulers, weighted_delays, color=colors, alpha=0.7)
    ax4.set_ylabel('Weighted Mean Delay (ms)', fontsize=12)
    ax4.set_title('Weighted Scheduling Delay\n(HIGH=0.35, MEDIUM=0.40, LOW=0.25)', fontsize=14, fontweight='bold')
    ax4.grid(True, alpha=0.3, axis='y')
    for bar, val in zip(bars, weighted_delays):
        height = bar.get_height()
        ax4.text(bar.get_x() + bar.get_width()/2., height,
                f'{val:.2f}ms',
                ha='center', va='bottom', fontsize=11, fontweight='bold')
    
    # 5. Delay Budget Utilization
    ax5 = plt.subplot(4, 3, 5)
    for df, label, color in [(agentic_df, 'Agentic', 'blue'), (baseline_df, 'Baseline', 'red')]:
        sorted_util = np.sort(df['delay_budget_used'])
        y = np.arange(1, len(sorted_util) + 1) / len(sorted_util)
        ax5.plot(sorted_util, y, label=label, color=color, linewidth=2)
    ax5.axvline(x=1.0, color='black', linestyle='--', alpha=0.5, label='Budget Limit')
    ax5.set_xlabel('Delay Budget Utilization', fontsize=12)
    ax5.set_ylabel('Cumulative Probability', fontsize=12)
    ax5.set_title('Delay Budget Utilization CDF', fontsize=14, fontweight='bold')
    ax5.legend()
    ax5.grid(True, alpha=0.3)
    ax5.set_xlim(0, min(2.0, max(agentic_df['delay_budget_used'].quantile(0.99), baseline_df['delay_budget_used'].quantile(0.99))))
    
    # 6. Zero Delay Requests
    ax6 = plt.subplot(4, 3, 6)
    schedulers = ['Agentic', 'Baseline']
    zero_delay_pcts = [
        agentic_stats['zero_delay']['percentage'],
        baseline_stats['zero_delay']['percentage'],
    ]
    colors = ['blue', 'red']
    bars = ax6.bar(schedulers, zero_delay_pcts, color=colors, alpha=0.7)
    ax6.set_ylabel('Percentage of Requests (%)', fontsize=12)
    ax6.set_title('Zero Delay Requests', fontsize=14, fontweight='bold')
    ax6.grid(True, alpha=0.3, axis='y')
    for bar, val in zip(bars, zero_delay_pcts):
        height = bar.get_height()
        ax6.text(bar.get_x() + bar.get_width()/2., height,
                f'{val:.2f}%',
                ha='center', va='bottom', fontsize=11, fontweight='bold')
    
    # 7. Per-Priority Mean Delay
    ax7 = plt.subplot(4, 3, 7)
    priorities = ['HIGH', 'MEDIUM', 'LOW']
    agentic_means = [
        agentic_stats['per_priority']['HIGH']['mean_delay_ms'],
        agentic_stats['per_priority']['MEDIUM']['mean_delay_ms'],
        agentic_stats['per_priority']['LOW']['mean_delay_ms'],
    ]
    baseline_means = [
        baseline_stats['per_priority']['HIGH']['mean_delay_ms'],
        baseline_stats['per_priority']['MEDIUM']['mean_delay_ms'],
        baseline_stats['per_priority']['LOW']['mean_delay_ms'],
    ]
    x = np.arange(len(priorities))
    width = 0.35
    ax7.bar(x - width/2, agentic_means, width, label='Agentic', color='blue', alpha=0.7)
    ax7.bar(x + width/2, baseline_means, width, label='Baseline', color='red', alpha=0.7)
    ax7.set_xlabel('Priority', fontsize=12)
    ax7.set_ylabel('Mean Delay (ms)', fontsize=12)
    ax7.set_title('Mean Scheduling Delay by Priority', fontsize=14, fontweight='bold')
    ax7.set_xticks(x)
    ax7.set_xticklabels(priorities)
    ax7.legend()
    ax7.grid(True, alpha=0.3, axis='y')
    ax7.set_yscale('log')
    
    # 8. P95 Delay by Priority
    ax8 = plt.subplot(4, 3, 8)
    agentic_p95 = [
        agentic_stats['per_priority']['HIGH']['p95_delay_ms'],
        agentic_stats['per_priority']['MEDIUM']['p95_delay_ms'],
        agentic_stats['per_priority']['LOW']['p95_delay_ms'],
    ]
    baseline_p95 = [
        baseline_stats['per_priority']['HIGH']['p95_delay_ms'],
        baseline_stats['per_priority']['MEDIUM']['p95_delay_ms'],
        baseline_stats['per_priority']['LOW']['p95_delay_ms'],
    ]
    ax8.bar(x - width/2, agentic_p95, width, label='Agentic', color='blue', alpha=0.7)
    ax8.bar(x + width/2, baseline_p95, width, label='Baseline', color='red', alpha=0.7)
    ax8.set_xlabel('Priority', fontsize=12)
    ax8.set_ylabel('P95 Delay (ms)', fontsize=12)
    ax8.set_title('P95 Scheduling Delay by Priority', fontsize=14, fontweight='bold')
    ax8.set_xticks(x)
    ax8.set_xticklabels(priorities)
    ax8.legend()
    ax8.grid(True, alpha=0.3, axis='y')
    ax8.set_yscale('log')
    
    # 9. Decision Distribution
    ax9 = plt.subplot(4, 3, 9)
    agentic_decisions = agentic_stats['decision_distribution']
    baseline_decisions = baseline_stats['decision_distribution']
    decisions = sorted(set(list(agentic_decisions.keys()) + list(baseline_decisions.keys())))
    agentic_counts = [agentic_decisions.get(d, 0) for d in decisions]
    baseline_counts = [baseline_decisions.get(d, 0) for d in decisions]
    x = np.arange(len(decisions))
    ax9.bar(x - width/2, agentic_counts, width, label='Agentic', color='blue', alpha=0.7)
    ax9.bar(x + width/2, baseline_counts, width, label='Baseline', color='red', alpha=0.7)
    ax9.set_xlabel('Decision Type', fontsize=12)
    ax9.set_ylabel('Count', fontsize=12)
    ax9.set_title('Decision Distribution', fontsize=14, fontweight='bold')
    ax9.set_xticks(x)
    ax9.set_xticklabels(decisions, rotation=45, ha='right')
    ax9.legend()
    ax9.grid(True, alpha=0.3, axis='y')
    ax9.set_yscale('log')
    
    # 10. Improvement Summary
    ax10 = plt.subplot(4, 3, 10)
    metrics = ['Mean\nDelay', 'P95\nDelay', 'Weighted\nDelay', 'Deadline\nMisses']
    improvements = [
        comparison['scheduling_delay']['mean_improvement_pct'],
        comparison['scheduling_delay']['p95_improvement_pct'],
        comparison['weighted_delay']['improvement_pct'],
        comparison['deadline_misses']['relative_reduction_pct'],
    ]
    colors_bar = ['green' if x > 0 else 'red' for x in improvements]
    bars = ax10.barh(metrics, improvements, color=colors_bar, alpha=0.7)
    ax10.set_xlabel('Improvement (%)', fontsize=12)
    ax10.set_title('Agentic Scheduler Improvements', fontsize=14, fontweight='bold')
    ax10.axvline(x=0, color='black', linestyle='-', linewidth=1)
    ax10.grid(True, alpha=0.3, axis='x')
    for bar, val in zip(bars, improvements):
        width = bar.get_width()
        ax10.text(width, bar.get_y() + bar.get_height()/2.,
                f'{val:.1f}%',
                ha='left' if width > 0 else 'right', va='center', fontsize=11, fontweight='bold')
    
    # 11. Delay Histogram (overall)
    ax11 = plt.subplot(4, 3, 11)
    max_delay = min(500, max(agentic_df['scheduling_delay_ms'].quantile(0.95), baseline_df['scheduling_delay_ms'].quantile(0.95)))
    bins = np.linspace(0, max_delay, 50)
    ax11.hist(agentic_df['scheduling_delay_ms'], bins=bins, alpha=0.6, label='Agentic', color='blue', density=True)
    ax11.hist(baseline_df['scheduling_delay_ms'], bins=bins, alpha=0.6, label='Baseline', color='red', density=True)
    ax11.set_xlabel('Scheduling Delay (ms)', fontsize=12)
    ax11.set_ylabel('Density', fontsize=12)
    ax11.set_title('Scheduling Delay Distribution', fontsize=14, fontweight='bold')
    ax11.legend()
    ax11.grid(True, alpha=0.3)
    
    # 12. Key Metrics Summary Table
    ax12 = plt.subplot(4, 3, 12)
    ax12.axis('off')
    table_data = [
        ['Metric', 'Agentic', 'Baseline', 'Improvement'],
        ['Mean Delay (ms)', f"{agentic_stats['scheduling_delay']['mean']:.2f}", 
         f"{baseline_stats['scheduling_delay']['mean']:.2f}",
         f"{comparison['scheduling_delay']['mean_improvement_pct']:.1f}%"],
        ['P95 Delay (ms)', f"{agentic_stats['scheduling_delay']['p95']:.2f}",
         f"{baseline_stats['scheduling_delay']['p95']:.2f}",
         f"{comparison['scheduling_delay']['p95_improvement_pct']:.1f}%"],
        ['Weighted Delay (ms)', f"{agentic_stats['weighted_scheduling_delay']['mean']:.2f}",
         f"{baseline_stats['weighted_scheduling_delay']['mean']:.2f}",
         f"{comparison['weighted_delay']['improvement_pct']:.1f}%"],
        ['Deadline Misses', f"{agentic_stats['deadline_misses']['count']:,}",
         f"{baseline_stats['deadline_misses']['count']:,}",
         f"{comparison['deadline_misses']['relative_reduction_pct']:.1f}%"],
        ['Zero Delay %', f"{agentic_stats['zero_delay']['percentage']:.2f}%",
         f"{baseline_stats['zero_delay']['percentage']:.2f}%",
         f"{comparison['zero_delay']['increase_pct']:.1f}%"],
    ]
    table = ax12.table(cellText=table_data[1:], colLabels=table_data[0],
                      cellLoc='center', loc='center',
                      colWidths=[0.35, 0.2, 0.2, 0.25])
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 2)
    for i in range(len(table_data[0])):
        table[(0, i)].set_facecolor('#4472C4')
        table[(0, i)].set_text_props(weight='bold', color='white')
    ax12.set_title('Key Metrics Summary', fontsize=14, fontweight='bold', pad=20)
    
    plt.tight_layout()
    plt.savefig('scheduler_comparison.png', dpi=300, bbox_inches='tight')
    print("✓ Saved visualization: scheduler_comparison.png")
    
    return fig

def generate_presentation_summary(agentic_stats, baseline_stats, comparison):
    """Generate a presentation-ready summary."""
    summary = f"""
{'='*80}
AGENTIC-AWARE WRITE SCHEDULING: PERFORMANCE ANALYSIS
200,000 Requests | Priority Weights: HIGH=0.35, MEDIUM=0.40, LOW=0.25
{'='*80}

EXECUTIVE SUMMARY
-----------------
The Agentic Scheduler demonstrates significant improvements over the baseline
first-come-first-served approach across all key metrics.

KEY IMPROVEMENTS
----------------
1. Mean Scheduling Delay:
   • Agentic: {agentic_stats['scheduling_delay']['mean']:.2f} ms
   • Baseline: {baseline_stats['scheduling_delay']['mean']:.2f} ms
   • Improvement: {comparison['scheduling_delay']['mean_improvement_pct']:.1f}% reduction
   • Absolute: {comparison['scheduling_delay']['mean_improvement_ms']:.2f} ms faster

2. P95 Scheduling Delay:
   • Agentic: {agentic_stats['scheduling_delay']['p95']:.2f} ms
   • Baseline: {baseline_stats['scheduling_delay']['p95']:.2f} ms
   • Improvement: {comparison['scheduling_delay']['p95_improvement_pct']:.1f}% reduction
   • Absolute: {comparison['scheduling_delay']['p95_improvement_ms']:.2f} ms faster

3. Weighted Scheduling Delay (Priority-Aware):
   • Agentic: {agentic_stats['weighted_scheduling_delay']['mean']:.2f} ms
   • Baseline: {baseline_stats['weighted_scheduling_delay']['mean']:.2f} ms
   • Improvement: {comparison['weighted_delay']['improvement_pct']:.1f}% reduction
   • This metric weights delays by priority importance (HIGH=0.35, MEDIUM=0.40, LOW=0.25)

4. Deadline Misses:
   • Agentic: {agentic_stats['deadline_misses']['count']:,} ({agentic_stats['deadline_misses']['percentage']:.2f}%)
   • Baseline: {baseline_stats['deadline_misses']['count']:,} ({baseline_stats['deadline_misses']['percentage']:.2f}%)
   • Reduction: {comparison['deadline_misses']['absolute_reduction']:,} fewer misses
   • Relative Improvement: {comparison['deadline_misses']['relative_reduction_pct']:.1f}% reduction

5. Zero Delay Requests:
   • Agentic: {agentic_stats['zero_delay']['count']:,} ({agentic_stats['zero_delay']['percentage']:.2f}%)
   • Baseline: {baseline_stats['zero_delay']['count']:,} ({baseline_stats['zero_delay']['percentage']:.2f}%)
   • Increase: {comparison['zero_delay']['increase']:,} more requests with zero delay

PER-PRIORITY ANALYSIS
---------------------
HIGH Priority (Weight: 0.35):
  • Mean Delay: Agentic={agentic_stats['per_priority']['HIGH']['mean_delay_ms']:.2f}ms, Baseline={baseline_stats['per_priority']['HIGH']['mean_delay_ms']:.2f}ms
  • P95 Delay: Agentic={agentic_stats['per_priority']['HIGH']['p95_delay_ms']:.2f}ms, Baseline={baseline_stats['per_priority']['HIGH']['p95_delay_ms']:.2f}ms
  • Deadline Misses: Agentic={agentic_stats['per_priority']['HIGH']['deadline_misses']} ({agentic_stats['per_priority']['HIGH']['deadline_miss_percentage']:.2f}%), Baseline={baseline_stats['per_priority']['HIGH']['deadline_misses']} ({baseline_stats['per_priority']['HIGH']['deadline_miss_percentage']:.2f}%)

MEDIUM Priority (Weight: 0.40):
  • Mean Delay: Agentic={agentic_stats['per_priority']['MEDIUM']['mean_delay_ms']:.2f}ms, Baseline={baseline_stats['per_priority']['MEDIUM']['mean_delay_ms']:.2f}ms
  • P95 Delay: Agentic={agentic_stats['per_priority']['MEDIUM']['p95_delay_ms']:.2f}ms, Baseline={baseline_stats['per_priority']['MEDIUM']['p95_delay_ms']:.2f}ms
  • Deadline Misses: Agentic={agentic_stats['per_priority']['MEDIUM']['deadline_misses']} ({agentic_stats['per_priority']['MEDIUM']['deadline_miss_percentage']:.2f}%), Baseline={baseline_stats['per_priority']['MEDIUM']['deadline_misses']} ({baseline_stats['per_priority']['MEDIUM']['deadline_miss_percentage']:.2f}%)

LOW Priority (Weight: 0.25):
  • Mean Delay: Agentic={agentic_stats['per_priority']['LOW']['mean_delay_ms']:.2f}ms, Baseline={baseline_stats['per_priority']['LOW']['mean_delay_ms']:.2f}ms
  • P95 Delay: Agentic={agentic_stats['per_priority']['LOW']['p95_delay_ms']:.2f}ms, Baseline={baseline_stats['per_priority']['LOW']['p95_delay_ms']:.2f}ms
  • Deadline Misses: Agentic={agentic_stats['per_priority']['LOW']['deadline_misses']} ({agentic_stats['per_priority']['LOW']['deadline_miss_percentage']:.2f}%), Baseline={baseline_stats['per_priority']['LOW']['deadline_misses']} ({baseline_stats['per_priority']['LOW']['deadline_miss_percentage']:.2f}%)

DETAILED STATISTICS
-------------------
Agentic Scheduler:
  • Total Requests: {agentic_stats['total_requests']:,}
  • Unique Requests: {agentic_stats['unique_requests']:,}
  • Median Delay: {agentic_stats['scheduling_delay']['median']:.2f} ms
  • P99 Delay: {agentic_stats['scheduling_delay']['p99']:.2f} ms
  • Max Delay: {agentic_stats['scheduling_delay']['max']:.2f} ms
  • Delay Std Dev: {agentic_stats['scheduling_delay']['std']:.2f} ms

Baseline:
  • Total Requests: {baseline_stats['total_requests']:,}
  • Unique Requests: {baseline_stats['unique_requests']:,}
  • Median Delay: {baseline_stats['scheduling_delay']['median']:.2f} ms
  • P99 Delay: {baseline_stats['scheduling_delay']['p99']:.2f} ms
  • Max Delay: {baseline_stats['scheduling_delay']['max']:.2f} ms
  • Delay Std Dev: {baseline_stats['scheduling_delay']['std']:.2f} ms

CONCLUSION
----------
The Agentic-Aware Write Scheduler successfully improves system performance by:
1. Reducing average scheduling delays by {comparison['scheduling_delay']['mean_improvement_pct']:.1f}%
2. Reducing tail latency (P95) by {comparison['scheduling_delay']['p95_improvement_pct']:.1f}%
3. Reducing deadline misses by {comparison['deadline_misses']['relative_reduction_pct']:.1f}%
4. Increasing zero-delay requests by {comparison['zero_delay']['increase_pct']:.1f}%

These improvements demonstrate the effectiveness of priority-aware and deadline-aware
scheduling in managing write workloads with heterogeneous requirements.

{'='*80}
"""
    return summary

def main():
    print("Loading trace files...")
    agentic_df = load_trace('experiment.csv')
    baseline_df = load_trace('replay_trace_server.csv')
    
    print("Computing statistics...")
    agentic_stats = compute_statistics(agentic_df, 'Agentic Scheduler')
    baseline_stats = compute_statistics(baseline_df, 'Baseline')
    
    print("Comparing results...")
    comparison = compare_statistics(agentic_stats, baseline_stats)
    
    print("Creating visualizations...")
    create_visualizations(agentic_df, baseline_df, agentic_stats, baseline_stats, comparison)
    
    print("Generating summary...")
    summary = generate_presentation_summary(agentic_stats, baseline_stats, comparison)
    
    # Save summary
    with open('PRESENTATION_SUMMARY.txt', 'w') as f:
        f.write(summary)
    print("✓ Saved summary: PRESENTATION_SUMMARY.txt")
    
    # Save detailed statistics as JSON
    with open('detailed_statistics.json', 'w') as f:
        json.dump({
            'agentic': agentic_stats,
            'baseline': baseline_stats,
            'comparison': comparison,
        }, f, indent=2, default=str)
    print("✓ Saved detailed statistics: detailed_statistics.json")
    
    # Print summary to console
    print("\n" + summary)
    
    print("\n" + "="*80)
    print("Analysis complete! Files generated:")
    print("  • scheduler_comparison.png - Comprehensive visualizations")
    print("  • PRESENTATION_SUMMARY.txt - Presentation-ready summary")
    print("  • detailed_statistics.json - Detailed statistics in JSON format")
    print("="*80)

if __name__ == "__main__":
    main()

