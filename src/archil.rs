use crate::model::{shuffle, tail_chars, Observation, RunReport, TASKS};
use reqwest::blocking::Client;
use serde::Deserialize;
use serde_json::json;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc};
use std::time::{Duration, Instant};

const WORKER_SCRIPT: &str = include_str!("guest_worker.py");
const IMAGE: &str = "python:3.13-slim";

#[derive(Clone)]
struct ArchilClient {
    client: Client,
    base_url: String,
    api_key: String,
    disk_id: String,
}

#[derive(Deserialize)]
struct Envelope { success: bool, data: Option<ExecData>, error: Option<String>, code: Option<String> }

/// HTTP statuses that will not improve by sending more requests in this run:
/// bad credentials, plan/allowance limits, missing disk, or rate limiting.
fn is_fatal_status(status: u16) -> bool {
    matches!(status, 401 | 403 | 404 | 429)
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ExecData {
    exit_code: i32,
    stdout: String,
    stderr: String,
    timing: Option<Timing>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Timing { total_ms: Option<f64>, queue_ms: Option<f64>, execute_ms: Option<f64> }

#[derive(Deserialize)]
struct WorkResult { elapsed_ms: f64, operations: u64, bytes: u64, valid: bool, #[serde(default)] cleanup_error: Option<String> }

fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn region_endpoint(region: &str) -> Result<&'static str, String> {
    match region {
        "aws-us-east-1" => Ok("https://control.green.us-east-1.aws.prod.archil.com"),
        "aws-us-west-2" => Ok("https://control.green.us-west-2.aws.prod.archil.com"),
        "aws-eu-west-1" => Ok("https://control.green.eu-west-1.aws.prod.archil.com"),
        _ => Err("Unsupported serverless region. Use one of: aws-us-east-1, aws-us-west-2, aws-eu-west-1".into()),
    }
}

fn validate_id(disk_id: &str) -> Result<(), String> {
    if disk_id.len() != 20 || !disk_id.starts_with("dsk-") || !disk_id[4..].bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err("ARCHIL_DISK_ID must look like dsk- followed by 16 hexadecimal characters".into());
    }
    Ok(())
}

/// Returns the observation plus `true` if the run must stop (see `is_fatal_status`).
fn exec_task(api: &ArchilClient, task: &str, trial: usize, fanout: usize, scope: &str, size_mib: usize) -> (Observation, bool) {
    let mut result = Observation {
        task: task.into(), trial, fanout, ok: false, client_wall_ms: 0.0,
        remote_total_ms: None, remote_queue_ms: None, remote_execute_ms: None,
        work_ms: None, operations: None, bytes: None, error: None, cleanup_error: None, started_ms: None, parallel: None,
    };
    // Every invocation receives an isolated scratch subdirectory.
    let path = format!("/mnt/archil/.diskscope/{scope}/{task}-{trial}-{fanout}");
    let command = format!("python3 -c {} {} {} {}", shell_quote(WORKER_SCRIPT), shell_quote(task), shell_quote(&path), size_mib);
    let url = format!("{}/api/disks/{}/exec", api.base_url, api.disk_id);
    // Measure the full client-observed request, including response-body download.
    // reqwest::blocking::Response::json() consumes the body; timing only .send()
    // would undercount network and response parsing overhead.
    let start = Instant::now();
    let response = api.client.post(&url)
        .bearer_auth(&api.api_key)
        .json(&json!({"command": command, "baseImage": IMAGE}))
        .send()
        .and_then(|response| {
            let status = response.status();
            let body = response.bytes()?;
            Ok((status, body))
        });
    result.client_wall_ms = start.elapsed().as_secs_f64() * 1000.0;
    let (status, body) = match response {
        Ok(response) => response,
        Err(e) => { result.error = Some(format!("Network/request error: {e}")); return (result, false); }
    };
    if !status.is_success() {
        // Surface Archil's documented {error, code} body (e.g. code=allowance_exhausted); never echo request data.
        let detail = serde_json::from_slice::<Envelope>(&body).ok()
            .map(|e| format!("{} (code={})", e.error.unwrap_or_default(), e.code.unwrap_or_else(|| "none".into())))
            .unwrap_or_default();
        result.error = Some(format!("Archil returned HTTP {status}: {}", detail.chars().take(300).collect::<String>()));
        return (result, is_fatal_status(status.as_u16()));
    }
    let envelope: Envelope = match serde_json::from_slice(&body) {
        Ok(parsed) => parsed,
        Err(e) => { result.error = Some(format!("Unrecognized API response: {e}")); return (result, false); }
    };
    if !envelope.success {
        result.error = Some(envelope.error.unwrap_or_else(|| "Unknown Archil API error".into()));
        return (result, false);
    }
    let data = match envelope.data {
        Some(data) => data,
        None => { result.error = Some("Archil response omitted data".into()); return (result, false); }
    };
    if let Some(t) = data.timing {
        result.remote_total_ms = t.total_ms;
        result.remote_queue_ms = t.queue_ms;
        result.remote_execute_ms = t.execute_ms;
    }
    if data.exit_code != 0 {
        result.error = Some(format!("Guest returned {}: {}", data.exit_code, tail_chars(&data.stderr, 600)));
        return (result, false);
    }
    const MARKER: &str = "DISKSCOPE_JSON=";
    let line = data.stdout.lines().find_map(|line| line.strip_prefix(MARKER));
    let Some(line) = line else {
        result.error = Some("Guest stdout did not include DISKSCOPE_JSON marker".into());
        return (result, false);
    };
    let work: WorkResult = match serde_json::from_str(line) {
        Ok(work) => work,
        Err(e) => { result.error = Some(format!("Guest result could not be decoded: {e}")); return (result, false); }
    };
    result.work_ms = Some(work.elapsed_ms);
    result.operations = Some(work.operations);
    result.bytes = Some(work.bytes);
    result.ok = work.valid;
    result.cleanup_error = work.cleanup_error;
    if !work.valid { result.error = Some("Guest post-run assertion failed".into()); }
    (result, false)
}

fn stop_early(mut report: RunReport) -> RunReport {
    let reason = report.observations.last().and_then(|o| o.error.clone()).unwrap_or_default();
    eprintln!("STOPPING EARLY: non-retryable Archil response; no further calls sent. {reason}");
    report.environment.notes.push_str(" RUN STOPPED EARLY after a non-retryable API response (401/403/404/429).");
    report
}

pub fn run(runs: usize, size_mib: usize, fanout_levels: &[usize], parallel: usize) -> Result<RunReport, String> {
    let key = std::env::var("ARCHIL_API_KEY").map_err(|_| "Set ARCHIL_API_KEY to run against Archil".to_owned())?;
    let disk_id = std::env::var("ARCHIL_DISK_ID").map_err(|_| "Set ARCHIL_DISK_ID for an existing dedicated benchmark disk".to_owned())?;
    let region = std::env::var("ARCHIL_REGION").unwrap_or_else(|_| "aws-us-west-2".into());
    validate_id(&disk_id)?;
    let base_url = region_endpoint(&region)?.to_owned();
    let client = Client::builder()
        .timeout(Duration::from_secs(310))
        .build()
        .map_err(|e| e.to_string())?;
    let api = Arc::new(ArchilClient { client, api_key:key, disk_id: disk_id.clone(), base_url });
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    let scope = format!("{}-{}", std::process::id(), now.as_secs());
    let seed = now.as_nanos() as u64 ^ std::process::id() as u64;
    let run_start = Instant::now();
    let mut report = RunReport::empty("archil", scope.clone(), size_mib, runs);
    report.environment.parallel = Some(parallel);
    report.environment.shuffle_seed = Some(seed);
    report.environment.region = Some(region);
    report.environment.disk_id_suffix = Some(disk_id[disk_id.len()-4..].to_owned());
    report.environment.image = Some(IMAGE.into());
    report.environment.notes = "Archil remote uses Python 3 stdlib guest, wall-clock spans API request to JSON response. queueMs includes VM provisioning and mounts, NOT raw Firecracker boot. seq-read uses just-created data and is NOT a cold-cache test. No true CPU stack samples from Archil.".into();
    if parallel > 1 {
        report.environment.notes.push_str(&format!(" Suite ran with up to {parallel} concurrent microVMs; concurrent calls may contend, so compare against parallel=1 runs before drawing latency conclusions."));
    }

    // First invocation warms image preparation; report its result explicitly rather than discarding it.
    println!("Image warmup (recorded but excluded from test suite): {IMAGE}");
    let (warm, _) = exec_task(&api, "noop", 0, 1, &scope, size_mib);
    println!("Warmup: {:.2} ms, success={}", warm.client_wall_ms, warm.ok);
    report.observations.push(Observation { task:"image-warmup".into(), started_ms: Some(0.0), parallel: Some(1), ..warm });
    if !report.observations[0].ok {
        return Err(format!("Warmup failed: {}", report.observations[0].error.as_deref().unwrap_or("unknown error")));
    }

    // Task suite: every (task, trial) pair in a seeded random order, so slow drift in the platform
    // or network is spread across tasks instead of landing on whichever task ran last.
    // `parallel` workers pull from one queue; each call is its own Archil microVM.
    let mut jobs: Vec<(&'static str, usize)> = TASKS.iter().flat_map(|&t| (0..runs).map(move |i| (t, i))).collect();
    shuffle(&mut jobs, seed);
    let jobs = Arc::new(jobs);
    let next = Arc::new(AtomicUsize::new(0));
    let stop = Arc::new(AtomicBool::new(false));
    let (tx, rx) = mpsc::channel::<(Observation, bool)>();
    let mut workers = Vec::with_capacity(parallel);
    for _ in 0..parallel {
        let (api, jobs, next, stop, tx, scope) = (Arc::clone(&api), Arc::clone(&jobs), Arc::clone(&next), Arc::clone(&stop), tx.clone(), scope.clone());
        workers.push(std::thread::spawn(move || loop {
            if stop.load(Ordering::SeqCst) { break; }
            let i = next.fetch_add(1, Ordering::SeqCst);
            let Some(&(task, trial)) = jobs.get(i) else { break };
            let submitted = run_start.elapsed().as_secs_f64() * 1000.0;
            let (mut obs, fatal) = exec_task(&api, task, trial, 1, &scope, size_mib);
            obs.started_ms = Some(submitted);
            obs.parallel = Some(parallel);
            if fatal { stop.store(true, Ordering::SeqCst); }
            if tx.send((obs, fatal)).is_err() { break; }
        }));
    }
    drop(tx);
    let total = jobs.len();
    let mut suite = Vec::with_capacity(total);
    let mut fatal_seen = false;
    for (done, (obs, fatal)) in rx.iter().enumerate() {
        println!("[{:>3}/{total}] {:<14} trial {:>2}  {:>9.2} ms  {}{}", done + 1, obs.task, obs.trial + 1, obs.client_wall_ms,
            if obs.ok {"OK"} else {"FAIL"}, if obs.cleanup_error.is_some() {"  (cleanup failed; see JSON)"} else {""});
        fatal_seen |= fatal;
        suite.push(obs);
    }
    for w in workers { w.join().map_err(|_| "Bench worker panicked".to_owned())?; }
    suite.sort_by(|a, b| a.started_ms.unwrap_or(0.0).total_cmp(&b.started_ms.unwrap_or(0.0)));
    report.observations.extend(suite);
    if fatal_seen { return Ok(stop_early(report)); }

    // Isolated fanout on read-only task. No shared path is mutated by these calls.
    for &fanout in fanout_levels {
        for trial in 0..runs {
            let mut handles = Vec::with_capacity(fanout);
            for _ in 0..fanout {
                let api = Arc::clone(&api);
                let scope = scope.clone();
                let submitted = run_start.elapsed().as_secs_f64() * 1000.0;
                handles.push(std::thread::spawn(move || {
                    let (mut obs, fatal) = exec_task(&api, "stat", trial, fanout, &scope, size_mib);
                    obs.started_ms = Some(submitted);
                    obs.parallel = Some(fanout);
                    (obs, fatal)
                }));
            }
            let mut fatal_seen = false;
            for handle in handles {
                let (mut observation, fatal) = handle.join().map_err(|_| "Bench worker panicked".to_owned())?;
                observation.task = "fanout-stat".into();
                report.observations.push(observation);
                fatal_seen |= fatal;
            }
            println!("fanout-stat: {fanout} concurrent request(s), round {}", trial + 1);
            if fatal_seen { return Ok(stop_early(report)); }
        }
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn quote_works() { assert_eq!(shell_quote("a'b"), "'a'\\''b'"); }
    #[test]
    fn allowlisted_regions() {
        assert!(region_endpoint("aws-us-west-2").is_ok());
        assert!(region_endpoint("https://evil.example").is_err());
    }
    #[test]
    fn fatal_statuses() {
        for s in [401, 403, 404, 429] { assert!(is_fatal_status(s)); }
        for s in [400, 500, 504] { assert!(!is_fatal_status(s)); }
    }
    #[test]
    fn disk_id() {
        assert!(validate_id("dsk-0123456789abcdef").is_ok());
        assert!(validate_id("dsk-zzzzzzzzzzzzzzzz").is_err());
    }
}
