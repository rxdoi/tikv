// Copyright 2025 TiKV Project Authors. Licensed under Apache-2.0.
//
// Agentic-Aware Write Scheduling (v0)
//
// Minimal, store-level scheduler used only for RawKV write paths in service layer.
// - Server-side reads scheduling metadata from gRPC headers (priority, deadline).
// - Uses a simple global counter to estimate available write slots.
// - Re-evaluates every BASE_RECHECK_DELAY_MS until threshold satisfied or urgency.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, Once};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tokio::time::sleep;
use std::thread;
use std::fs;
use std::io::Write;
use chrono::{NaiveDateTime, SecondsFormat, Utc};

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum AawsPriority {
    High,
    Medium,
    Low,
}

#[derive(Clone, Debug)]
pub struct AawsMeta {
    pub priority: AawsPriority,
    // absolute deadline in milliseconds since UNIX_EPOCH
    pub deadline_ms: u64,
    pub actual_key: Vec<u8>,
    // server-side tracing context
    pub request_id: String,
    pub arrival_time_ms: u64,
    pub delay_budget_ms: u64,
}

// Global knobs - dynamically updated based on scheduler pool size
// Base ratios: High:Medium:Low = 1:2:4 (sum = 7, leaving 1 slot buffer)
// These are scaled proportionally based on actual pool size
static THRESHOLD_HIGH: AtomicUsize = AtomicUsize::new(1);
static THRESHOLD_MEDIUM: AtomicUsize = AtomicUsize::new(2);
static THRESHOLD_LOW: AtomicUsize = AtomicUsize::new(4);
static MAX_WORKER_SLOTS: AtomicUsize = AtomicUsize::new(8); // virtual write slots for availability estimation

const BASE_RECHECK_DELAY_MS: u64 = 5;
const URGENCY_MARGIN_MS: u64 = 10;

static RUNNING_WRITES: AtomicUsize = AtomicUsize::new(0);

// --------- Trace collection (server-side) ----------
#[derive(Clone, Debug)]
pub struct AawsSchedRecord {
    pub request_id: String,
    pub priority: AawsPriority,
    pub arrival_time_ms: u64,
    pub deadline_ms: u64,
    pub delay_budget_ms: u64,
    pub scheduled_time_ms: u64,
    pub scheduling_delay_ms: u64,
    pub available_threads_at_schedule: usize,
    pub required_threads: usize,
    pub decision: &'static str, // "immediate" | "delayed"
}

static TRACE_VEC: Mutex<Vec<AawsSchedRecord>> = Mutex::new(Vec::new());
static TRACE_WRITER_ONCE: Once = Once::new();

#[inline]
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_else(|_| Duration::from_millis(0))
        .as_millis() as u64
}

#[inline]
fn get_available_threads() -> usize {
    let running = RUNNING_WRITES.load(Ordering::Relaxed);
    let max_slots = MAX_WORKER_SLOTS.load(Ordering::Relaxed);
    max_slots.saturating_sub(running)
}

#[inline]
fn required_by_priority(p: AawsPriority) -> usize {
    match p {
        AawsPriority::High => THRESHOLD_HIGH.load(Ordering::Relaxed),
        AawsPriority::Medium => THRESHOLD_MEDIUM.load(Ordering::Relaxed),
        AawsPriority::Low => THRESHOLD_LOW.load(Ordering::Relaxed),
    }
}

#[inline]
pub fn inc_running() {
    RUNNING_WRITES.fetch_add(1, Ordering::Relaxed);
}

/// Atomically try to reserve 1 slot (each write uses 1 slot).
/// Returns true if reservation succeeded, false otherwise.
/// The 'required' parameter is the minimum available slots needed for this priority,
/// but we only reserve 1 slot per write.
#[inline]
pub fn try_reserve_slot(required_min_available: usize) -> bool {
    loop {
        let current = RUNNING_WRITES.load(Ordering::Acquire);
        let max_slots = MAX_WORKER_SLOTS.load(Ordering::Relaxed);
        let available = max_slots.saturating_sub(current);
        if available < required_min_available {
            return false;
        }
        // Try to atomically reserve 1 slot
        match RUNNING_WRITES.compare_exchange_weak(
            current,
            current + 1,
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            Ok(_) => return true,
            Err(_) => continue, // Retry on failure (another thread modified it)
        }
    }
}

#[inline]
pub fn dec_running() {
    RUNNING_WRITES.fetch_sub(1, Ordering::Relaxed);
}

#[inline]
pub fn available_threads() -> usize {
    get_available_threads()
}

#[inline]
pub fn required_threads_for_priority(p: AawsPriority) -> usize {
    required_by_priority(p)
}

pub fn record_scheduling_event(rec: AawsSchedRecord) {
    // Push to in-memory buffer
    if let Ok(mut vec) = TRACE_VEC.lock() {
        vec.push(rec);
    }
}

#[inline]
fn record_from_meta(meta: &AawsMeta, decision: &'static str, event_time_ms: u64, avail: usize, required: usize) {
    let scheduling_delay_ms = event_time_ms.saturating_sub(meta.arrival_time_ms);
    record_scheduling_event(AawsSchedRecord {
        request_id: meta.request_id.clone(),
        priority: meta.priority,
        arrival_time_ms: meta.arrival_time_ms,
        deadline_ms: meta.deadline_ms,
        delay_budget_ms: meta.delay_budget_ms,
        scheduled_time_ms: event_time_ms,
        scheduling_delay_ms,
        available_threads_at_schedule: avail,
        required_threads: required,
        decision,
    });
}

/// Initialize agent scheduler with the actual scheduler pool size.
/// This sets MAX_WORKER_SLOTS and scales thresholds proportionally.
/// 
/// Scaling logic:
/// - Base ratios: High:Medium:Low = 1:2:4 (sum = 7)
/// - MAX_WORKER_SLOTS = pool_size (matches actual capacity)
/// - Thresholds are scaled proportionally: threshold = (base_threshold * pool_size) / 8
/// - Minimum values: High >= 1, Medium >= 1, Low >= 1
pub fn init_agent_scheduler(pool_size: usize) {
    // Ensure pool_size is at least 1
    let pool_size = pool_size.max(1);
    
    // Set MAX_WORKER_SLOTS to match actual pool size
    MAX_WORKER_SLOTS.store(pool_size, Ordering::Release);
    
    // Scale thresholds proportionally from base ratios (1:2:4 at pool_size=8)
    // Formula: threshold = (base_threshold * pool_size + 4) / 8
    // The +4 ensures rounding up for better distribution
    let high = ((1 * pool_size + 4) / 8).max(1);
    let medium = ((2 * pool_size + 4) / 8).max(1);
    let low = ((4 * pool_size + 4) / 8).max(1);
    
    THRESHOLD_HIGH.store(high, Ordering::Release);
    THRESHOLD_MEDIUM.store(medium, Ordering::Release);
    THRESHOLD_LOW.store(low, Ordering::Release);
}

/// Update agent scheduler when pool size changes at runtime.
/// This is called when scheduler_worker_pool_size is changed dynamically.
pub fn update_agent_scheduler_pool_size(pool_size: usize) {
    init_agent_scheduler(pool_size);
}

pub fn ensure_trace_writer_started() {
    TRACE_WRITER_ONCE.call_once(|| {
        thread::spawn(|| {
            // Periodically write the entire CSV snapshot to a temp file then atomically rename.
            // File path relative to TiKV working directory.
            let output_path = "replay_trace_server.csv";
            let tmp_path = "replay_trace_server.csv.tmp";
            // Create header immediately to make file visible even before first event.
            if let Ok(mut f) = fs::File::create(tmp_path) {
                let _ = writeln!(
                    f,
                    "request_id,priority,arrival_ts,deadline_ts,delay_budget_ms,scheduled_ts,scheduling_delay_ms,available_threads_at_schedule,required_threads,decision"
                );
                let _ = f.flush();
                let _ = fs::rename(tmp_path, output_path);
            }
            loop {
                // Sleep first to batch early bursts.
                thread::sleep(Duration::from_millis(10));
                let snapshot = {
                    if let Ok(vec) = TRACE_VEC.lock() {
                        vec.clone()
                    } else {
                        Vec::new()
                    }
                };
                if snapshot.is_empty() {
                    continue;
                }
                // Sort by arrival_time_ms, then scheduled_time_ms to produce a stable timeline.
                let mut snapshot = snapshot;
                snapshot.sort_by(|a, b| {
                    a.arrival_time_ms
                        .cmp(&b.arrival_time_ms)
                        .then_with(|| a.scheduled_time_ms.cmp(&b.scheduled_time_ms))
                });
                // Write CSV
                if let Ok(mut f) = fs::File::create(tmp_path) {
                    let _ = writeln!(
                        f,
                        "request_id,priority,arrival_ts,deadline_ts,delay_budget_ms,scheduled_ts,scheduling_delay_ms,available_threads_at_schedule,required_threads,decision"
                    );
                    for r in snapshot.iter() {
                        let pri_str = match r.priority {
                            AawsPriority::High => "HIGH",
                            AawsPriority::Medium => "MEDIUM",
                            AawsPriority::Low => "LOW",
                        };
                        let at = NaiveDateTime::from_timestamp_opt(
                            (r.arrival_time_ms / 1000) as i64,
                            ((r.arrival_time_ms % 1000) as u32) * 1_000_000,
                        ).unwrap_or_else(|| NaiveDateTime::from_timestamp_opt(0, 0).unwrap());
                        let dt = NaiveDateTime::from_timestamp_opt(
                            (r.deadline_ms / 1000) as i64,
                            ((r.deadline_ms % 1000) as u32) * 1_000_000,
                        ).unwrap_or_else(|| NaiveDateTime::from_timestamp_opt(0, 0).unwrap());
                        let st = NaiveDateTime::from_timestamp_opt(
                            (r.scheduled_time_ms / 1000) as i64,
                            ((r.scheduled_time_ms % 1000) as u32) * 1_000_000,
                        ).unwrap();
                        let at_s = chrono::DateTime::<Utc>::from_utc(at, Utc).to_rfc3339_opts(SecondsFormat::Nanos, true);
                        let dt_s = chrono::DateTime::<Utc>::from_utc(dt, Utc).to_rfc3339_opts(SecondsFormat::Nanos, true);
                        let st_s = chrono::DateTime::<Utc>::from_utc(st, Utc).to_rfc3339_opts(SecondsFormat::Nanos, true);
                        let _ = writeln!(
                            f,
                            "{},{},{},{},{},{},{},{},{},{}",
                            r.request_id,
                            pri_str,
                            at_s,
                            dt_s,
                            r.delay_budget_ms,
                            st_s,
                            r.scheduling_delay_ms,
                            r.available_threads_at_schedule,
                            r.required_threads,
                            r.decision
                        );
                    }
                    let _ = f.flush();
                    let _ = fs::rename(tmp_path, output_path);
                }
            }
        });
    });
}

/// Returns true if a slot was reserved atomically, false if urgent admission happened without reservation
pub async fn maybe_delay_until_sched(meta: &AawsMeta) -> bool {
    // Background re-check loop (per-request)
    let required = required_by_priority(meta.priority);
    loop {
        let t = now_ms();
        if t + URGENCY_MARGIN_MS >= meta.deadline_ms {
            // urgent admit - try to reserve slot, but allow even if it fails (deadline approaching)
            let avail = get_available_threads();
            if try_reserve_slot(required) {
                record_from_meta(meta, "urgent-admit", t, avail, required);
                return true; // Slot reserved
            } else {
                // Still admit urgently even if we can't reserve (deadline approaching)
                record_from_meta(meta, "urgent-admit", t, avail, required);
                return false; // No slot reserved - caller must call inc_running()
            }
        }
        let avail = get_available_threads();
        // Atomically try to reserve 1 slot (checking that at least 'required' are available)
        if try_reserve_slot(required) {
            // scheduled - slot reserved atomically
            let t = now_ms();
            record_from_meta(meta, "scheduled", t, avail, required);
            return true; // Slot reserved
        }
        // not enough slots, record a check
        record_from_meta(meta, "check-delay", t, avail, required);
        sleep(Duration::from_millis(BASE_RECHECK_DELAY_MS)).await;
    }
}

pub fn block_delay_until_sched(meta: &AawsMeta) {
    loop {
        let t = now_ms();
        if t + URGENCY_MARGIN_MS >= meta.deadline_ms {
            // urgent admit, record and return
            let avail = get_available_threads();
            let required = required_by_priority(meta.priority);
            record_from_meta(meta, "urgent-admit", t, avail, required);
            return;
        }
        let avail = get_available_threads();
        let required = required_by_priority(meta.priority);
        if avail >= required {
            // scheduled
            let t2 = now_ms();
            record_from_meta(meta, "scheduled", t2, avail, required);
            return;
        }
        // not enough slots, record a check
        record_from_meta(meta, "check-delay", t, avail, required);
        thread::sleep(Duration::from_millis(BASE_RECHECK_DELAY_MS));
    }
}

