#!/bin/zsh
# Script to detect when all requests are done processing
# Usage: ./detect_completion.sh [input_csv_file] [trace_file] [timeout_seconds]

INPUT_CSV="${1:-requests_1million.csv}"
TRACE_FILE="${2:-replay_trace_server.csv}"
TIMEOUT="${3:-300}"  # Default 5 minutes timeout
STABLE_PERIOD=30     # File must be stable for 30 seconds

echo "Monitoring for request completion..."
echo "Input CSV: $INPUT_CSV"
echo "Trace file: $TRACE_FILE"
echo "Timeout: ${TIMEOUT}s"
echo ""

# Count expected requests (subtract 1 for header)
if [ ! -f "$INPUT_CSV" ]; then
    echo "Error: Input CSV file not found: $INPUT_CSV"
    exit 1
fi
EXPECTED=$(($(wc -l < "$INPUT_CSV") - 1))
echo "Expected requests: $EXPECTED"

# Method 1: File stability check (wait for file to stop growing)
echo ""
echo "Method 1: Monitoring file stability..."
prev_size=0
stable_count=0
start_time=$(date +%s)

while true; do
    current_time=$(date +%s)
    elapsed=$((current_time - start_time))
    
    if [ $elapsed -gt $TIMEOUT ]; then
        echo "Timeout reached (${TIMEOUT}s). Stopping."
        break
    fi
    
    if [ ! -f "$TRACE_FILE" ]; then
        echo "[${elapsed}s] Trace file not found yet..."
        sleep 5
        continue
    fi
    
    current_size=$(stat -f%z "$TRACE_FILE" 2>/dev/null || echo "0")
    current_lines=$(wc -l < "$TRACE_FILE" 2>/dev/null || echo "0")
    
    if [ "$current_size" = "$prev_size" ]; then
        stable_count=$((stable_count + 1))
        if [ $stable_count -ge 6 ]; then  # 6 checks * 5s = 30s stable
            echo "[${elapsed}s] ✓ File stable for ${STABLE_PERIOD}s"
            echo "   Final size: $current_size bytes"
            echo "   Final lines: $current_lines"
            break
        fi
    else
        stable_count=0
        prev_size=$current_size
    fi
    
    echo "[${elapsed}s] Size: $current_size bytes, Lines: $current_lines (stable: ${stable_count}/6)"
    sleep 5
done

# Method 2: Count-based check
echo ""
echo "Method 2: Count-based verification..."
if [ -f "$TRACE_FILE" ]; then
    # Count unique request IDs (subtract 1 for header)
    ACTUAL=$(($(tail -n +2 "$TRACE_FILE" 2>/dev/null | cut -d',' -f1 | sort -u | wc -l)))
    echo "Expected unique requests: $EXPECTED"
    echo "Actual unique requests in trace: $ACTUAL"
    
    if [ $ACTUAL -ge $EXPECTED ]; then
        echo "✓ All expected requests have been processed"
    else
        echo "⚠ Missing $((EXPECTED - ACTUAL)) requests"
    fi
else
    echo "Trace file not found"
fi

# Method 3: Check for recent activity
echo ""
echo "Method 3: Recent activity check..."
if [ -f "$TRACE_FILE" ]; then
    last_modified=$(stat -f%m "$TRACE_FILE" 2>/dev/null || echo "0")
    now=$(date +%s)
    age=$((now - last_modified))
    
    echo "File last modified: ${age}s ago"
    if [ $age -gt $STABLE_PERIOD ]; then
        echo "✓ No activity for ${age}s - likely complete"
    else
        echo "⚠ Recent activity detected (${age}s ago)"
    fi
fi

echo ""
echo "Done."

