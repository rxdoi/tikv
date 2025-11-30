import csv
import random
from datetime import datetime, timedelta

# Configuration
NUM_REQUESTS = 50000
OUTPUT_FILE = 'requests_50000.csv'

# Priority distribution (similar to original data)
PRIORITIES = ['High', 'Normal', 'Low']
PRIORITY_WEIGHTS = [0.35, 0.40, 0.25]  # Approximate distribution from original

# Request max delay ranges by priority (in milliseconds)
# Based on typical TiKV latency patterns with realistic distributions
DELAY_RANGES = {
    'High': {
        'mean': 12,
        'std': 4,
        'min': 5,
        'max': 20
    },
    'Normal': {
        'mean': 80,
        'std': 50,
        'min': 10,
        'max': 200
    },
    'Low': {
        'mean': 150,
        'std': 120,
        'min': 15,
        'max': 500
    }
}

def generate_delay(priority):
    """Generate realistic delay using normal distribution with clipping."""
    config = DELAY_RANGES[priority]
    # Use normal distribution for more realistic clustering
    delay = random.gauss(config['mean'], config['std'])
    # Clip to min/max bounds
    delay = max(config['min'], min(config['max'], delay))
    return round(delay, 3)

# Generate timestamp within a time window
start_time = datetime(2025, 11, 17, 16, 50, 54)
end_time = datetime(2025, 11, 17, 16, 51, 44)
time_diff_seconds = (end_time - start_time).total_seconds()

print(f"Generating {NUM_REQUESTS:,} request records...")

with open(OUTPUT_FILE, 'w', newline='') as csvfile:
    writer = csv.writer(csvfile)
    
    # Write header
    writer.writerow([
        'arrival_time',
        'request_id',
        'request_type',
        'call_time',
        'request_max_delay',
        'priority',
        'key',
        'value'
    ])
    
    # Generate records
    for i in range(1, NUM_REQUESTS + 1):
        # Generate random timestamp
        random_seconds = random.uniform(0, time_diff_seconds)
        timestamp = start_time + timedelta(seconds=random_seconds)
        timestamp_str = timestamp.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + '000'
        
        # Select priority
        priority = random.choices(PRIORITIES, weights=PRIORITY_WEIGHTS)[0]
        
        # Generate request_max_delay based on priority (in milliseconds)
        request_max_delay = generate_delay(priority)
        
        # Generate request ID with padding
        request_id = f'R{i:04d}'
        
        # Generate key and value
        key = f'key_{i:04d}'
        value = f'value_{i:04d}'
        
        # Write row
        writer.writerow([
            timestamp_str,
            request_id,
            'Actual',
            timestamp_str,
            request_max_delay,
            priority,
            key,
            value
        ])
        
        # Progress indicator
        if i % 100000 == 0:
            print(f"  Progress: {i:,} / {NUM_REQUESTS:,} ({i/NUM_REQUESTS*100:.1f}%)")

print(f"\nCompleted! File saved as: {OUTPUT_FILE}")
print(f"Total records: {NUM_REQUESTS:,}")