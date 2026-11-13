use crate::model::{shuffle, tail_chars, Observation, RunReport, TASKS, WORKER_SCRIPT};
use reqwest::blocking::Client;
use serde::Deserialize;
use serde_json::json;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc};
use std::time::{Duration, Instant};

/// `--image default` omits `baseImage`, so calls are served from Archil's pool of ready microVMs
/// (docs: "Default image and size start from a pool of ready microVMs"). Any other value is sent as
/// `baseImage` and boots on demand, which measured ~110 ms more queue time per call.
pub const DEFAULT_IMAGE: &str = "default";

struct ArchilClient {
    client: Client,
    base_url: String,
    api_key: String,
    disk_id: String,
    base_image: Option<String>,
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

/// OCI references are passed through as JSON (never a shell), but keep them to a sane alphabet.
pub fn validate_image(image: &str) -> Result<(), String> {
    let ok = !image.is_empty() && image.len() <= 200
        && image.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-/:@".contains(&b));
    if ok { Ok(()) } else { Err("--image must be 'default' or an OCI reference like python:3.13-slim".into()) }
}

/// The remote command: run the embedded guest with `-S` (skip `site`; measured ~2-30 ms of start-up).
fn guest_command(task: &str, path: &str, size_mib: usize) -> String {
    format!("python3 -S -c {} {} {} {}", shell_quote(WORKER_SCRIPT), shell_quote(task), shell_quote(path), size_mib)
}

/// Returns the observation plus `true` if the run must stop (see `is_fatal_status`).
fn exec_task(api: &ArchilClient, task: &str, trial: usize, fanout: usize, scope: &str, size_mib: usize) -> (Observation, bool) {
    let mut result = Observation::new(task, trial, fanout);
    // Every invocation gets a unique scratch name below the run's scope.
    let path = format!("/mnt/archil/.diskscope/{scope}/{task}-{trial}-{fanout}");
    let mut body = json!({ "command": guest_command(task, &path, size_mib) });
    if let Some(image) = &api.base_image { body["baseImage"] = json!(image); }
    let url = format!("{}/api/disks/{}/exec", api.base_url, api.disk_id);
    // Time the full client-observed request including the response-body download; JSON parsing
    // happens after the clock stops.
    let start = Instant::now();
    let response = api.client.post(&url)
        .bearer_auth(&api.api_key)
        .json(&body)
        .send()
        .and_then(|response| {
            let status = response.status();
            let body = response.bytes()?;
            Ok((status, body))
        });
    result.client_wall_ms = start.elapsed().as_secs_f64() * 1000.0;
    let (status, body) = match response {
        Ok(response) => response,
        // without_url(): reqwest's Display includes the request URL, which contains the full disk ID.
        Err(e) => { result.error = Some(format!("Network/request error: {}", e.without_url())); return (result, false); }
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
    let data = match (envelope.success, envelope.data) {
        (true, Some(data)) => data,
        (true, None) => { result.error = Some("Archil response omitted data".into()); return (result, false); }
        (false, _) => { result.error = Some(envelope.error.unwrap_or_else(|| "Unknown Archil API error".into())); return (result, false); }
    };
    if let Some(t) = data.timing {
        result.remote_total_ms = t.total_ms;
        result.remote_queue_ms = t.queue_ms;
        result.remote_execute_ms = t.execute_ms;
    }
    let parsed = result.apply_guest_output(&data.stdout);
    if data.exit_code != 0 {
        result.ok = false;
        let reason = format!("Guest returned {}: {}", data.exit_code, tail_chars(&data.stderr, 600));
        result.error = Some(match result.error.take() { Some(prev) => format!("{prev}; {reason}"), None => reason });
    } else if !parsed {
        result.error = Some("Guest stdout did not include a decodable DISKSCOPE_JSON line".into());
    }
    (result, false)
}

/// Remove this run's scratch directory. A default exec mount holds write delegations only on what that
/// exec created, so it cannot delete directories made by earlier execs (EROFS). One call through the
/// multi-disk endpoint with a `conditional` mount (writes without a delegation checkout) removes them.
/// Only `.diskscope/<scope>` is touched; the outcome is recorded, never fatal.
fn cleanup_scope(api: &ArchilClient, scope: &str) -> String {
    let body = json!({
        "disks": { "d": { "disk": api.disk_id, "conditional": true } },
        "command": format!("rm -rf {} && echo removed", shell_quote(&format!("/mnt/archil/d/.diskscope/{scope}"))),
    });
    let response = api.client.post(format!("{}/api/exec", api.base_url)).bearer_auth(&api.api_key).json(&body).send()
        .and_then(|r| { let ok = r.status().is_success(); r.bytes().map(|b| (ok, b)) });
    match response {
        Ok((true, b)) => match serde_json::from_slice::<Envelope>(&b).ok().and_then(|e| e.data) {
            Some(d) if d.exit_code == 0 => "scratch directory removed".into(),
            Some(d) => format!("scratch cleanup failed: {}", tail_chars(&d.stderr, 200)),
            None => "scratch cleanup: unexpected response".into(),
        },
        Ok((false, _)) => "scratch cleanup: HTTP error".into(),
        Err(e) => format!("scratch cleanup: {}", e.without_url()),
    }
}

fn stop_early(mut report: RunReport, reason: &str) -> RunReport {
    eprintln!("STOPPING EARLY: non-retryable Archil response; no further calls sent. {reason}");
    report.environment.notes.push_str(&format!(" RUN STOPPED EARLY after a non-retryable API response: {reason}"));
    report
}

/// Issue `n` concurrent warm-up calls, recorded as `image-warmup` (excluded from every statistic).
/// They prepare a non-default image, claim warm VMs, and open the TLS connections the measured
/// calls will reuse, so the first measured round at any concurrency doesn't pay a handshake.
fn warm_up(api: &Arc<ArchilClient>, n: usize, scope: &str, size_mib: usize, run_start: Instant) -> Result<Vec<Observation>, String> {
    let handles: Vec<_> = (0..n).map(|i| {
        let (api, scope) = (Arc::clone(api), scope.to_owned());
        std::thread::spawn(move || {
            let submitted = run_start.elapsed().as_secs_f64() * 1000.0;
            let (mut obs, fatal) = exec_task(&api, "noop", i, n, &scope, size_mib);
            obs.task = "image-warmup".into();
            obs.started_ms = Some(submitted);
            obs.parallel = Some(n);
            (obs, fatal)
        })
    }).collect();
    let mut out = Vec::with_capacity(n);
    for h in handles {
        let (obs, fatal) = h.join().map_err(|_| "Warm-up worker panicked".to_owned())?;
        if fatal || !obs.ok { return Err(format!("Warm-up failed: {}", obs.error.as_deref().unwrap_or("unknown error"))); }
        out.push(obs);
    }
    Ok(out)
}

pub fn run(runs: usize, size_mib: usize, fanout_levels: &[usize], parallel: usize, image: &str) -> Result<RunReport, String> {
    let key = std::env::var("ARCHIL_API_KEY").map_err(|_| "Set ARCHIL_API_KEY to run against Archil".to_owned())?;
    let disk_id = std::env::var("ARCHIL_DISK_ID").map_err(|_| "Set ARCHIL_DISK_ID for an existing dedicated benchmark disk".to_owned())?;
    let region = std::env::var("ARCHIL_REGION").unwrap_or_else(|_| "aws-us-west-2".into());
    validate_id(&disk_id)?;
    validate_image(image)?;
    let base_url = region_endpoint(&region)?.to_owned();
    let client = Client::builder()
        .timeout(Duration::from_secs(310))
        .pool_max_idle_per_host(8)
        .build()
        .map_err(|e| e.to_string())?;
    let base_image = (image != DEFAULT_IMAGE).then(|| image.to_owned());
    let api = Arc::new(ArchilClient { client, api_key: key, disk_id: disk_id.clone(), base_url, base_image });
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    let scope = format!("{}-{}", std::process::id(), now.as_secs());
    let seed = now.as_nanos() as u64 ^ std::process::id() as u64;
    let run_start = Instant::now();
    let mut report = RunReport::empty("archil", scope.clone(), size_mib, runs);
    report.environment.parallel = Some(parallel);
    report.environment.shuffle_seed = Some(seed);
    report.environment.region = Some(region);
    report.environment.disk_id_suffix = Some(disk_id[disk_id.len()-4..].to_owned());
    report.environment.image = Some(if image == DEFAULT_IMAGE { "archil-default".into() } else { image.to_owned() });
    report.environment.variant = Some(format!("exec · {} image", if image == DEFAULT_IMAGE { "default" } else { image }));
    report.environment.notes = "Serverless exec: one fresh microVM per call running the stdlib Python guest with `python3 -S`. Client wall-clock spans request to full response body. queueMs includes VM claim/boot and mount. seq-read reads just-written data (warm cache).".into();
    if parallel > 1 {
        report.environment.notes.push_str(&format!(" Suite ran with up to {parallel} concurrent microVMs."));
    }

    let warm_n = parallel.max(fanout_levels.iter().copied().max().unwrap_or(1));
    println!("Warm-up: {warm_n} concurrent call(s) on the {image} image (recorded, excluded from statistics)");
    let warm = warm_up(&api, warm_n, &scope, size_mib, run_start)?;
    for w in &warm { println!("  warm-up {:.0} ms (queue {:?} ms)", w.client_wall_ms, w.remote_queue_ms); }
    report.observations.extend(warm);

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
    let mut fatal_reason: Option<String> = None;
    for (done, (obs, fatal)) in rx.iter().enumerate() {
        println!("[{:>3}/{total}] {:<14} trial {:>2}  {:>9.2} ms  {}{}", done + 1, obs.task, obs.trial + 1, obs.client_wall_ms,
            if obs.ok {"OK"} else {"FAIL"}, if obs.cleanup_error.is_some() {"  (cleanup failed; see JSON)"} else {""});
        if fatal && fatal_reason.is_none() { fatal_reason = Some(obs.error.clone().unwrap_or_default()); }
        suite.push(obs);
    }
    for w in workers { w.join().map_err(|_| "Bench worker panicked".to_owned())?; }
    suite.sort_by(|a, b| a.started_ms.unwrap_or(0.0).total_cmp(&b.started_ms.unwrap_or(0.0)));
    report.observations.extend(suite);
    if let Some(reason) = fatal_reason { return Ok(stop_early(report, &reason)); }
    run_fanout(&api, &mut report, fanout_levels, runs, &scope, size_mib, run_start)?;
    let outcome = cleanup_scope(&api, &scope);
    println!("Cleanup: {outcome}");
    report.environment.notes.push_str(&format!(" Cleanup: {outcome}."));
    Ok(report)
}

fn run_fanout(api: &Arc<ArchilClient>, report: &mut RunReport, fanout_levels: &[usize], runs: usize, scope: &str, size_mib: usize, run_start: Instant) -> Result<(), String> {
    // Fan-out of the read-only stat task at each concurrency level.
    for &fanout in fanout_levels {
        for trial in 0..runs {
            let handles: Vec<_> = (0..fanout).map(|_| {
                let (api, scope) = (Arc::clone(api), scope.to_owned());
                let submitted = run_start.elapsed().as_secs_f64() * 1000.0;
                std::thread::spawn(move || {
                    let (mut obs, fatal) = exec_task(&api, "stat", trial, fanout, &scope, size_mib);
                    obs.task = "fanout-stat".into();
                    obs.started_ms = Some(submitted);
                    obs.parallel = Some(fanout);
                    (obs, fatal)
                })
            }).collect();
            let mut fatal_reason = None;
            for handle in handles {
                let (obs, fatal) = handle.join().map_err(|_| "Bench worker panicked".to_owned())?;
                if fatal && fatal_reason.is_none() { fatal_reason = obs.error.clone(); }
                report.observations.push(obs);
            }
            println!("fanout-stat: {fanout} concurrent request(s), round {}", trial + 1);
            if let Some(reason) = fatal_reason {
                let stopped = stop_early(std::mem::replace(report, RunReport::empty("archil", String::new(), 0, 0)), &reason);
                *report = stopped;
                return Ok(());
            }
        }
    }
    Ok(())
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
    #[test]
    fn image_validation() {
        for ok in ["default", "python:3.13-slim", "ghcr.io/org/img@sha256:abc"] { assert!(validate_image(ok).is_ok(), "{ok}"); }
        for bad in ["", "python; rm -rf /", "a b", "$(x)"] { assert!(validate_image(bad).is_err(), "{bad}"); }
    }
    #[test]
    fn guest_command_skips_site_and_quotes() {
        let cmd = guest_command("noop", "/mnt/archil/.diskscope/r/noop-0-1", 4);
        assert!(cmd.starts_with("python3 -S -c '"));
        assert!(cmd.ends_with(" 'noop' '/mnt/archil/.diskscope/r/noop-0-1' 4"));
        assert!(cmd.len() < 100 * 1024, "Archil rejects commands over 100 KiB");
    }
}
