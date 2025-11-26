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
}

// Global knobs (v0: consts; can be turned into config later)
const THRESHOLD_HIGH: usize = 1;
const THRESHOLD_MEDIUM: usize = 2;
const THRESHOLD_LOW: usize = 4;
const BASE_RECHECK_DELAY_MS: u64 = 5;
const URGENCY_MARGIN_MS: u64 = 10;
const MAX_WORKER_SLOTS: usize = 8; // virtual write slots for availability estimation

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
    MAX_WORKER_SLOTS.saturating_sub(running)
}

#[inline]
fn required_by_priority(p: AawsPriority) -> usize {
    match p {
        AawsPriority::High => THRESHOLD_HIGH,
        AawsPriority::Medium => THRESHOLD_MEDIUM,
        AawsPriority::Low => THRESHOLD_LOW,
    }
}

#[inline]
pub fn inc_running() {
    RUNNING_WRITES.fetch_add(1, Ordering::Relaxed);
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

pub fn ensure_trace_writer_started() {
    TRACE_WRITER_ONCE.call_once(|| {
        thread::spawn(|| {
            // Periodically write the entire CSV snapshot to a temp file then atomically rename.
            // File path relative to TiKV working directory.
            let output_path = "replay_trace.csv";
            let tmp_path = "replay_trace.csv.tmp";
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
                        "request_id,priority,arrival_time_ms,deadline_ms,delay_budget_ms,scheduled_time_ms,scheduling_delay_ms,available_threads_at_schedule,required_threads,decision"
                    );
                    for r in snapshot.iter() {
                        let pri_str = match r.priority {
                            AawsPriority::High => "HIGH",
                            AawsPriority::Medium => "MEDIUM",
                            AawsPriority::Low => "LOW",
                        };
                        let _ = writeln!(
                            f,
                            "{},{},{},{},{},{},{},{},{},{}",
                            r.request_id,
                            pri_str,
                            r.arrival_time_ms,
                            r.deadline_ms,
                            r.delay_budget_ms,
                            r.scheduled_time_ms,
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

pub async fn maybe_delay_until_sched(meta: &AawsMeta) {
    // Background re-check loop (per-request)
    loop {
        let t = now_ms();
        if t + URGENCY_MARGIN_MS >= meta.deadline_ms {
            // urgent
            return;
        }
        let avail = get_available_threads();
        let required = required_by_priority(meta.priority);
        if avail >= required {
            return;
        }
        sleep(Duration::from_millis(BASE_RECHECK_DELAY_MS)).await;
    }
}

pub fn block_delay_until_sched(meta: &AawsMeta) {
    loop {
        let t = now_ms();
        if t + URGENCY_MARGIN_MS >= meta.deadline_ms {
            return;
        }
        let avail = get_available_threads();
        let required = required_by_priority(meta.priority);
        if avail >= required {
            return;
        }
        thread::sleep(Duration::from_millis(BASE_RECHECK_DELAY_MS));
    }
}

