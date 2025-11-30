#!/bin/bash
# Run 1M request experiment for both scheduler and baseline

set -e

echo "=" | cat
echo "RUNNING 1 MILLION REQUEST EXPERIMENT"
echo "=" | cat

# Check if files exist
if [ ! -f "requests_1million.csv" ]; then
    echo "❌ Error: requests_1million.csv not found"
    exit 1
fi

echo ""
echo "📋 This script will:"
echo "  1. Run scheduler experiment (trace_actual_1m.csv)"
echo "  2. Run baseline experiment (replay_trace_server_1m.csv)"
echo "  3. Compare results"
echo ""
echo "⚠️  Note: This will take a while (1M requests)"
echo ""
read -p "Continue? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
fi

# Backup existing trace files
echo ""
echo "📦 Backing up existing trace files..."
if [ -f "replay_trace_server.csv" ]; then
    mv replay_trace_server.csv replay_trace_server_50k_backup.csv
fi
if [ -f "trace_actual.csv" ]; then
    mv trace_actual.csv trace_actual_50k_backup.csv
fi

# Check if TiKV is running
if ! pgrep -f tikv-server > /dev/null; then
    echo "❌ Error: TiKV server is not running"
    echo "   Start it with: ./target/release/tikv-server --addr=\"127.0.0.1:20160\" --data-dir=tikv-data --pd=\"127.0.0.1:2379\" > logs/tikv.log 2>&1 &"
    exit 1
fi

echo ""
echo "🚀 Starting scheduler experiment..."
echo "   (Make sure you're on the scheduler branch)"
rm -f replay_trace_server.csv trace_actual.csv
sleep 2

./go-ycsb/bin/csv-ycsb \
    -csv ./requests_1million.csv \
    -pd 127.0.0.1:2379 \
    -table usertable \
    -apiversion V1

# Wait for trace file to be written
echo ""
echo "⏳ Waiting for trace file to be written..."
sleep 10

if [ -f "replay_trace_server.csv" ]; then
    mv replay_trace_server.csv trace_actual_1m.csv
    echo "✅ Scheduler trace saved: trace_actual_1m.csv"
else
    echo "⚠️  Warning: replay_trace_server.csv not found"
fi

echo ""
echo "🔄 Now switch to baseline branch and run again"
echo "   Then run: ./analyze_1m_comparison.py"

