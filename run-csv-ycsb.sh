cd /Users/gifted/test_tikv/tikv

# Stop existing TiKV (ignore error if none)
pkill -f tikv-server || true

# Start fresh TiKV
mkdir -p logs
./target/release/tikv-server \
  --addr="127.0.0.1:20160" \
  --data-dir=tikv-data \
  --pd="127.0.0.1:2379" \
  > logs/tikv.log 2>&1 &

# Wait for TiKV to come up
sleep 5

# Run CSV YCSB
./go-ycsb/bin/csv-ycsb \
  -csv ./requests_1million.csv \
  -pd 127.0.0.1:2379 \
  -table usertable \
  -apiversion V1