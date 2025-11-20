// Copyright 2025 TiKV Project Authors. Licensed under Apache-2.0.
//
// Agentic-Aware Write Scheduling (v0)
//
// Minimal, store-level scheduler used only for RawKV write paths in service layer.
// - Server-side reads scheduling metadata from gRPC headers (priority, deadline).
// - Uses a simple global counter to estimate available write slots.
// - Re-evaluates every BASE_RECHECK_DELAY_MS until threshold satisfied or urgency.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tokio::time::sleep;
use std::thread;

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

