package main

import (
	"context"
	"encoding/csv"
	"flag"
	"fmt"
	"google.golang.org/grpc/metadata"
	"go.uber.org/atomic"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/magiconair/properties"
	"github.com/pingcap/go-ycsb/pkg/measurement"
	"github.com/pingcap/go-ycsb/pkg/prop"
	"github.com/pingcap/kvproto/pkg/kvrpcpb"
	"github.com/tikv/client-go/v2/rawkv"
)

// record models one CSV row we care about.
type record struct {
	arrival time.Duration // offset from the first arrival in the file
	maxDelayMs uint64     // local deadline = baseStart + arrival + maxDelayMs
	priority string       // "H"|"M"|"L"
	key     string
	value   string
}

func parseTimeFlexible(s string) (time.Time, error) {
	layouts := []string{
		"2006-01-02T15:04:05.999999999",
		"2006-01-02T15:04:05.999999",
		"2006-01-02T15:04:05.999",
		"2006-01-02T15:04:05",
		time.RFC3339Nano,
		time.RFC3339,
	}
	var lastErr error
	for _, l := range layouts {
		t, err := time.Parse(l, s)
		if err == nil {
			return t, nil
		}
		lastErr = err
	}
	return time.Time{}, lastErr
}

func loadCSV(path string) ([]record, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	r := csv.NewReader(f)
	r.FieldsPerRecord = -1
	rows, err := r.ReadAll()
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, fmt.Errorf("empty csv: %s", path)
	}
	// header indices
	h := make(map[string]int)
	for i, name := range rows[0] {
		h[strings.TrimSpace(name)] = i
	}
	required := []string{"arrival_time", "request_max_delay", "priority", "key", "value"}
	for _, k := range required {
		if _, ok := h[k]; !ok {
			return nil, fmt.Errorf("csv missing required column: %s", k)
		}
	}
	type rowParsed struct {
		arrivalAbs time.Time
		maxDelayMs uint64
		priority string
		key        string
		value      string
	}
	outAbs := make([]rowParsed, 0, len(rows)-1)
	for i := 1; i < len(rows); i++ {
		row := rows[i]
		if len(strings.TrimSpace(strings.Join(row, ""))) == 0 {
			continue
		}
		atStr := row[h["arrival_time"]]
		key := row[h["key"]]
		val := row[h["value"]]
		if key == "" {
			continue
		}
		tm, err := parseTimeFlexible(strings.TrimSpace(atStr))
		if err != nil {
			return nil, fmt.Errorf("parse arrival_time on line %d: %w", i+1, err)
		}
		prio := strings.TrimSpace(row[h["priority"]])
		maxdStr := strings.TrimSpace(row[h["request_max_delay"]])
		maxd, _ := strconv.ParseFloat(maxdStr, 64) // seconds (can be float)
		outAbs = append(outAbs, rowParsed{
			arrivalAbs: tm,
			maxDelayMs: uint64(maxd * 1000.0),
			priority: prio,
			key:        key,
			value:      val,
		})
	}
	if len(outAbs) == 0 {
		return nil, fmt.Errorf("no valid rows in csv")
	}
	// find base time
	base := outAbs[0].arrivalAbs
	for _, rp := range outAbs {
		if rp.arrivalAbs.Before(base) {
			base = rp.arrivalAbs
		}
	}
	out := make([]record, 0, len(outAbs))
	for _, rp := range outAbs {
		delta := rp.arrivalAbs.Sub(base)
		if delta < 0 {
			delta = 0
		}
		// map CSV priority to H/M/L
		code := "M"
		switch strings.ToLower(rp.priority) {
		case "high":
			code = "H"
		case "low":
			code = "L"
		case "normal", "medium":
			code = "M"
		}
		out = append(out, record{
			arrival: delta,
			maxDelayMs: rp.maxDelayMs,
			priority: code,
			key:     rp.key,
			value:   rp.value,
		})
	}
	return out, nil
}

// AAWS v0 client-side scheduling knobs
const (
	thresholdHigh        = 1
	thresholdMedium      = 2
	thresholdLow         = 4
	maxWorkerSlots       = 8
	baseRecheckDelayMs   = 5
	urgencyMarginMs      = 10
)

func requiredByPriority(p string) int {
	switch strings.ToUpper(p) {
	case "H":
		return thresholdHigh
	case "L":
		return thresholdLow
	default:
		return thresholdMedium
	}
}

func main() {
	var (
		csvPath  string
		pd       string
		apiver   string
		table    string
		verbose  bool
		maxWaitS int
		noScheduling bool
		tracePath string
	)
	flag.StringVar(&csvPath, "csv", "/Users/xuandi_ren/Desktop/tikv/delay_sample_requests.csv", "CSV file path")
	flag.StringVar(&pd, "pd", "127.0.0.1:2379", "PD endpoints, comma separated")
	flag.StringVar(&apiver, "apiversion", "V1", "TiKV API version: V1 or V2")
	flag.StringVar(&table, "table", "usertable", "Key prefix table name (used only to namespace keys)")
	flag.BoolVar(&verbose, "v", false, "Verbose logging")
	flag.IntVar(&maxWaitS, "max-wait-seconds", 0, "Max wait seconds before skipping a late record (0 means no limit)")
	flag.BoolVar(&noScheduling, "no-scheduling", false, "Disable v0 client-side scheduling; send once at arrival time")
	flag.StringVar(&tracePath, "trace", "", "If non-empty, write per-op trace CSV with arrival/send/finish timestamps")
	flag.Parse()

	records, err := loadCSV(csvPath)
	if err != nil {
		fmt.Printf("failed to load csv: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("Loaded %d records from %s\n", len(records), csvPath)

	// Init measurement in histogram mode to mimic go-ycsb output.
	props := properties.NewProperties()
	props.Set(prop.MeasurementType, "histogram")
	measurement.InitMeasure(props)

	// Create RawKV client with API version
	ctx := context.Background()
	pdAddrs := strings.Split(pd, ",")
	var api kvrpcpb.APIVersion
	switch strings.ToUpper(apiver) {
	case "V1":
		api = kvrpcpb.APIVersion_V1
	case "V2":
		api = kvrpcpb.APIVersion_V2
	default:
		fmt.Printf("invalid -apiversion: %s (use V1 or V2)\n", apiver)
		os.Exit(2)
	}
	client, err := rawkv.NewClientWithOpts(ctx, pdAddrs, rawkv.WithAPIVersion(api))
	if err != nil {
		fmt.Printf("failed to create rawkv client: %v\n", err)
		os.Exit(1)
	}
	defer client.Close()

	baseStart := time.Now()
	var wg sync.WaitGroup
	var mu sync.Mutex
	var errorCount int
	opName := "INSERT"
	running := atomic.NewInt32(0)

	// Optional per-op trace collection
	type opResult struct {
		Index         int
		Key           string
		Priority      string
		MaxDelayMs    uint64
		ArrivalAt     time.Time
		SendAt        time.Time
		DoneAt        time.Time
		LatencyMicros int64
		DelayMs       int64
	}
	var results []opResult
	var resCh chan opResult
	if tracePath != "" {
		resCh = make(chan opResult, len(records))
	}

	for i, rec := range records {
		wg.Add(1)
		i := i
		rec := rec
		go func() {
			defer wg.Done()
			arrivalAt := baseStart.Add(rec.arrival)
			deadlineAt := arrivalAt.Add(time.Duration(rec.maxDelayMs) * time.Millisecond)
			now := time.Now()
			if arrivalAt.After(now) {
				time.Sleep(arrivalAt.Sub(now))
			} else if maxWaitS > 0 {
				// late arrival beyond threshold? skip
				if now.Sub(arrivalAt) > time.Duration(maxWaitS)*time.Second {
					if verbose {
						fmt.Printf("skip late record idx=%d late=%s\n", i, now.Sub(arrivalAt).String())
					}
					return
				}
			}
			// Use plain business key; metadata goes via gRPC headers (no key prefix injection).
			key := []byte(fmt.Sprintf("%s:%s", table, rec.key))
			val := []byte(rec.value)

			// Build gRPC metadata for server-side scheduling (only when scheduling is enabled).
			// Keys are lowercase per gRPC metadata convention.
			var putCtx context.Context = ctx
			if !noScheduling {
				md := metadata.Pairs(
					"x-aaws-priority", rec.priority,
					"x-aaws-arrival-ms", strconv.FormatInt(arrivalAt.UnixMilli(), 10),
					"x-aaws-deadline-ms", strconv.FormatInt(deadlineAt.UnixMilli(), 10),
				)
				putCtx = metadata.NewOutgoingContext(ctx, md)
			}

			// Timestamps for tracing
			var sendAt, doneAt time.Time
			var lat time.Duration

			if noScheduling {
				// Baseline: send once at arrival time (no thresholds, no urgency backoff).
				sendAt = time.Now()
				err := client.Put(putCtx, key, val)
				doneAt = time.Now()
				lat = doneAt.Sub(sendAt)
				measurement.Measure(opName, sendAt, lat)
				if err != nil {
					mu.Lock()
					errorCount++
					mu.Unlock()
					if verbose {
						fmt.Printf("Put error idx=%d key=%q err=%v lat_us=%s\n", i, rec.key, err, strconv.FormatInt(lat.Microseconds(), 10))
					}
				}
			} else {
				// v0 client-side scheduling loop
				for {
					now := time.Now()
					urgent := now.After(deadlineAt.Add(-time.Duration(urgencyMarginMs) * time.Millisecond))
					avail := maxWorkerSlots - int(running.Load())
					reqNeed := requiredByPriority(rec.priority)
					allow := avail >= reqNeed || urgent
					if allow {
						// acquire
						running.Inc()
						sendAt = time.Now()
						err := client.Put(putCtx, key, val)
						doneAt = time.Now()
						lat = doneAt.Sub(sendAt)
						measurement.Measure(opName, sendAt, lat)
						running.Dec()
						if err != nil {
							mu.Lock()
							errorCount++
							mu.Unlock()
							if verbose {
								fmt.Printf("Put error idx=%d key=%q err=%v lat_us=%s\n", i, rec.key, err, strconv.FormatInt(lat.Microseconds(), 10))
							}
						}
						break
					}
					// delay and re-check
					time.Sleep(time.Duration(baseRecheckDelayMs) * time.Millisecond)
					// If exceeded overall patience (optional), continue looping; urgent guard will eventually fire.
					if verbose && time.Now().After(deadlineAt) {
						fmt.Printf("request idx=%d became urgent/late; forcing schedule soon\n", i)
					}
				}
			}

			// Emit per-op trace if requested
			if tracePath != "" {
				delayMs := sendAt.Sub(arrivalAt).Milliseconds()
				if delayMs < 0 {
					delayMs = 0
				}
				resCh <- opResult{
					Index:         i + 1,
					Key:           rec.key,
					Priority:      rec.priority,
					MaxDelayMs:    rec.maxDelayMs,
					ArrivalAt:     arrivalAt,
					SendAt:        sendAt,
					DoneAt:        doneAt,
					LatencyMicros: lat.Microseconds(),
					DelayMs:       delayMs,
				}
			}
		}()
	}
	wg.Wait()

	// Write trace CSV if requested
	if tracePath != "" {
		close(resCh)
		for r := range resCh {
			results = append(results, r)
		}
		sort.Slice(results, func(i, j int) bool { return results[i].Index < results[j].Index })
		f, err := os.Create(tracePath)
		if err != nil {
			fmt.Printf("failed to create trace file: %v\n", err)
		} else {
			defer f.Close()
			w := csv.NewWriter(f)
			_ = w.Write([]string{"index", "key", "priority", "max_delay_ms", "arrival_ts", "send_ts", "done_ts", "delay_ms", "latency_us"})
			for _, r := range results {
				_ = w.Write([]string{
					strconv.Itoa(r.Index),
					r.Key,
					r.Priority,
					strconv.FormatUint(r.MaxDelayMs, 10),
					r.ArrivalAt.Format(time.RFC3339Nano),
					r.SendAt.Format(time.RFC3339Nano),
					r.DoneAt.Format(time.RFC3339Nano),
					strconv.FormatInt(r.DelayMs, 10),
					strconv.FormatInt(r.LatencyMicros, 10),
				})
			}
			w.Flush()
			if err := w.Error(); err != nil {
				fmt.Printf("failed to write trace file: %v\n", err)
			} else if true {
				fmt.Printf("trace written: %s (%d rows)\n", tracePath, len(results))
			}
		}
	}

	fmt.Println("**********************************************")
	fmt.Println("CSV replay finished")
	fmt.Printf("Errors: %d\n", errorCount)
	fmt.Println("**********************************************")
	measurement.Summary()
}


