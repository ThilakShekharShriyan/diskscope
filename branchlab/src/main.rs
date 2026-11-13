//! BranchLab v0.2: bounded multi-invocation state correctness probes.
//! Additive to DiskScope: emits its version-1 JSON schema.
use clap::{Parser, Subcommand};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const GUEST: &str = include_str!("guest_state.py");
/// Archil's default image (Ubuntu 22.04, Python 3.10) comes from the warm pool: ~30 ms queue,
/// where `python:3.13-slim` boots on demand and is sometimes 1.3–3.2 s (see ARCHIL-FINDINGS.md #5).
const IMAGE: &str = "default";
const MARKER: &str = "BRANCHLAB_JSON=";

#[derive(Parser)]
#[command(name = "branchlab", version, about = "Bounded Archil state correctness and handoff benchmark")]
struct Cli {
    #[command(subcommand)]
    command: SubCmd,
}

#[derive(Subcommand)]
enum SubCmd {
    /// Run identical guest actions locally in separate Python processes.
    Local {
        #[arg(long, default_value_t = 2)]
        runs: usize,
        #[arg(long, default_value_t = 4)]
        size_kib: usize,
        #[arg(long, default_value_t = 2)]
        fanout: usize,
        #[arg(long, default_value = ".diskscope-work")]
        dir: PathBuf,
        #[arg(long, default_value = "results/state-local.json")]
        output: PathBuf,
        #[command(flatten)]
        opts: CaseOpts,
    },
    /// Run state handoff tests against an EXISTING dedicated Archil disk.
    Archil {
        #[arg(long, default_value_t = 2)]
        runs: usize,
        #[arg(long, default_value_t = 4)]
        size_kib: usize,
        #[arg(long, default_value_t = 2)]
        fanout: usize,
        #[arg(long, default_value = "results/state-archil.json")]
        output: PathBuf,
        #[command(flatten)]
        opts: CaseOpts,
    },
}

#[derive(clap::Args, Clone)]
struct CaseOpts {
    /// Comma-separated cases: handoff, sqlite, readfan, writers, semantics, visibility.
    #[arg(long, value_delimiter = ',', default_value = "handoff,sqlite,readfan,writers,semantics")]
    cases: Vec<String>,
    /// visibility: how long the long-lived reader polls before giving up (5..280 s).
    #[arg(long, default_value_t = 120)]
    cap_secs: u64,
    /// visibility: seconds between submitting the reader and submitting the writer.
    #[arg(long, default_value_t = 6)]
    writer_delay_secs: u64,
}

const CASES: [&str; 6] = ["handoff", "sqlite", "readfan", "writers", "semantics", "visibility"];

#[derive(Clone)]
enum Backend {
    Local,
    Archil {
        client: Client,
        base_url: String,
        key: String,
        disk_id: String,
    },
}

#[derive(Serialize, Deserialize)]
struct Observation {
    task: String,
    trial: usize,
    fanout: usize,
    ok: bool,
    client_wall_ms: f64,
    remote_total_ms: Option<f64>,
    remote_queue_ms: Option<f64>,
    remote_execute_ms: Option<f64>,
    work_ms: Option<f64>,
    operations: Option<u64>,
    bytes: Option<u64>,
    error: Option<String>,
    /// Structured guest output for semantics and visibility probes.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    detail: Option<Value>,
}

#[derive(Serialize)]
struct Environment {
    region: Option<String>,
    disk_id_suffix: Option<String>,
    host_os: String,
    workload_language: String,
    image: Option<String>,
    size_mib: usize,
    runs_per_task: usize,
    notes: String,
}

#[derive(Serialize)]
struct RunReport {
    schema_version: u32,
    backend: String,
    run_id: String,
    timestamp_unix_secs: u64,
    environment: Environment,
    observations: Vec<Observation>,
}

#[derive(Deserialize)]
struct ExecEnvelope {
    success: bool,
    data: Option<ExecData>,
    error: Option<Value>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ExecData {
    exit_code: i32,
    stdout: String,
    stderr: String,
    timing: Option<ExecTiming>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ExecTiming {
    total_ms: Option<f64>,
    queue_ms: Option<f64>,
    execute_ms: Option<f64>,
}

#[derive(Deserialize)]
struct GuestResult {
    valid: bool,
    elapsed_ms: f64,
    operations: u64,
    bytes: u64,
    detail: Value,
}

fn endpoint(region: &str) -> Result<&'static str, String> {
    match region {
        "aws-us-east-1" => Ok("https://control.green.us-east-1.aws.prod.archil.com"),
        "aws-us-west-2" => Ok("https://control.green.us-west-2.aws.prod.archil.com"),
        "aws-eu-west-1" => Ok("https://control.green.eu-west-1.aws.prod.archil.com"),
        _ => Err("Archil execution regions supported here: aws-us-east-1, aws-us-west-2, aws-eu-west-1".into()),
    }
}

fn new_backend(use_archil: bool) -> Result<(Backend, Option<String>, Option<String>), String> {
    if !use_archil {
        return Ok((Backend::Local, None, None));
    }
    let region = std::env::var("ARCHIL_REGION").map_err(|_| "Missing ARCHIL_REGION (e.g. aws-us-east-1)".to_owned())?;
    let base = endpoint(&region)?.to_string();
    let key = std::env::var("ARCHIL_API_KEY")
        .map_err(|_| "Missing ARCHIL_API_KEY".to_owned())?;
    let disk_id = std::env::var("ARCHIL_DISK_ID")
        .map_err(|_| "Missing ARCHIL_DISK_ID".to_owned())?;
    if disk_id.len() != 20
        || !disk_id.starts_with("dsk-")
        || !disk_id[4..].bytes().all(|b| b.is_ascii_hexdigit())
    {
        return Err("ARCHIL_DISK_ID must be dsk- followed by 16 hex characters".into());
    }
    let suffix = disk_id[disk_id.len() - 4..].to_owned();
    let client = Client::builder()
        .timeout(Duration::from_secs(315))
        .build()
        .map_err(|e| e.to_string())?;
    Ok((Backend::Archil { client, base_url: base, key, disk_id }, Some(region), Some(suffix)))
}

fn tail_chars(s: &str, n: usize) -> String {
    let count = s.chars().count();
    s.chars().skip(count.saturating_sub(n)).collect()
}

fn sh_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn unique_run_id() -> String {
    let ns = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos();
    format!("{}-{ns}", std::process::id())
}

#[allow(clippy::too_many_arguments)]
fn request(backend: &Backend, action: &str, base: &str, size_kib: usize, worker: usize, fanout: usize, param: u64) -> (f64, Option<ExecData>, Option<String>) {
    let start = Instant::now();
    match backend {
        Backend::Local => {
            let child = Command::new("python3")
                .arg("-S").arg("-c").arg(GUEST)
                .arg(action).arg(base).arg(size_kib.to_string())
                .arg(worker.to_string()).arg(fanout.to_string()).arg(param.to_string())
                .output();
            let wall_ms = start.elapsed().as_secs_f64() * 1_000.0;
            match child {
                Err(e) => (wall_ms, None, Some(format!("Could not start Python: {e}"))),
                Ok(child) => (
                    wall_ms,
                    Some(ExecData {
                        exit_code: child.status.code().unwrap_or(-1),
                        stdout: String::from_utf8_lossy(&child.stdout).into_owned(),
                        stderr: String::from_utf8_lossy(&child.stderr).into_owned(),
                        timing: None,
                    }),
                    None,
                ),
            }
        }
        Backend::Archil { client, base_url, key, disk_id } => {
            // Arguments are fixed or validated. No user-provided shell commands.
            // A default single-disk exec holds no delegation checkout, so it cannot unlink entries in
            // directories an earlier exec created (EROFS; ARCHIL-FINDINGS.md #1). Cleanup therefore
            // runs through the multi-disk endpoint with a conditional mount at /mnt/archil/d.
            let (url, body) = if action == "cleanup" {
                let mounted = base.replacen("/mnt/archil/", "/mnt/archil/d/", 1);
                let cmd = format!("python3 -S -c {} {} {} {size_kib} {worker} {fanout} {param}",
                    sh_quote(GUEST), sh_quote(action), sh_quote(&mounted));
                (format!("{base_url}/api/exec"),
                 json!({"disks": {"d": {"disk": disk_id, "conditional": true}}, "command": cmd}))
            } else {
                let cmd = format!("python3 -S -c {} {} {} {size_kib} {worker} {fanout} {param}",
                    sh_quote(GUEST), sh_quote(action), sh_quote(base));
                (format!("{base_url}/api/disks/{disk_id}/exec"), json!({"command": cmd}))
            };
            let res = client.post(&url)
                .bearer_auth(key)
                .json(&body)
                .send()
                .and_then(|response| {
                    let status = response.status();
                    let bytes = response.bytes()?;
                    Ok((status, bytes))
                });
            let wall_ms = start.elapsed().as_secs_f64() * 1_000.0;
            let (status, bytes) = match res {
                Ok(v) => v,
                Err(e) => return (wall_ms, None, Some(format!("Archil network error: {}", e.without_url()))),
            };
            if !status.is_success() {
                // Don't publish raw response bodies or headers, which could contain sensitive data.
                return (wall_ms, None, Some(format!("Archil HTTP {status}; check credentials, region, rate limits")));
            }
            let env: ExecEnvelope = match serde_json::from_slice(&bytes) {
                Ok(v) => v,
                Err(e) => return (wall_ms, None, Some(format!("Archil response decode error: {e}"))),
            };
            if !env.success {
                let detail = env.error.as_ref().map(|v| v.to_string()).unwrap_or_else(|| "unknown".into());
                return (wall_ms, None, Some(format!("Archil API error: {}", detail.chars().take(180).collect::<String>())));
            }
            match env.data {
                Some(d) => (wall_ms, Some(d), None),
                None => (wall_ms, None, Some("No data in Archil response".into())),
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn observe(backend: &Backend, action: &str, task: &str, trial: usize, fanout: usize,
           base: &str, size_kib: usize, worker: usize, param: u64) -> Observation {
    let (wall, data, error) = request(backend, action, base, size_kib, worker, fanout, param);
    let mut o = Observation {
        task: task.into(), trial, fanout, ok: false,
        client_wall_ms: wall, remote_total_ms: None, remote_queue_ms: None,
        remote_execute_ms: None, work_ms: None,
        operations: None, bytes: None, error, detail: None,
    };
    if let Some(d) = data {
        if let Some(t) = d.timing {
            o.remote_total_ms = t.total_ms;
            o.remote_queue_ms = t.queue_ms;
            o.remote_execute_ms = t.execute_ms;
        }
        // Parse the guest's result first, so an integrity failure keeps its detail and timing
        // even though the guest then exits non-zero.
        match d.stdout.lines().find_map(|s| s.strip_prefix(MARKER)) {
            Some(line) => match serde_json::from_str::<GuestResult>(line) {
                Ok(g) => {
                    o.ok = g.valid && d.exit_code == 0;
                    o.work_ms = Some(g.elapsed_ms);
                    o.operations = Some(g.operations);
                    o.bytes = Some(g.bytes);
                    if !g.valid { o.error = Some(format!("Integrity check failed: {}", g.detail)); }
                    if g.detail.is_object() { o.detail = Some(g.detail); }
                }
                Err(e) => o.error = Some(format!("Guest JSON parse failed: {e}")),
            },
            None => o.error = Some("Guest did not return BRANCHLAB_JSON marker".into()),
        }
        if d.exit_code != 0 && o.error.as_deref().is_none_or(|e| !e.starts_with("Integrity")) {
            // Keep the END of stderr: the last traceback line names the actual error (EROFS, I/O error).
            o.error = Some(format!("Guest exit {}: {}", d.exit_code, tail_chars(d.stderr.trim_end(), 250)));
        }
    }
    o
}

fn panic_observation(task: &str, trial: usize, fanout: usize) -> Observation {
    Observation { task: task.to_owned(), trial, fanout, ok: false, client_wall_ms: 0.0,
        remote_total_ms: None, remote_queue_ms: None, remote_execute_ms: None,
        work_ms: None, operations: None, bytes: None, detail: None,
        error: Some("Rust worker thread panicked; no latency measurement available".into()) }
}

fn add(report: &mut RunReport, observation: Observation) -> bool {
    println!("{:24} trial {:>2} fanout {:>2}: {:>8.1} ms {}",
        observation.task, observation.trial + 1, observation.fanout,
        observation.client_wall_ms, if observation.ok { "PASS" } else { "FAIL" });
    let ok = observation.ok;
    report.observations.push(observation);
    ok
}

fn scoped(base_prefix: &str, id: &str, trial: usize, case: &str) -> String {
    format!("{base_prefix}/branchlab-{id}-{trial}-{case}")
}

#[allow(clippy::too_many_arguments)]
fn call(report: &mut RunReport, backend: &Backend, action: &str, task: &str,
        trial: usize, fanout: usize, base: &str, size_kib: usize, worker: usize) -> bool {
    add(report, observe(backend, action, task, trial, fanout, base, size_kib, worker, 0))
}

#[allow(clippy::too_many_arguments)]
fn run_case(report: &mut RunReport, backend: &Backend, case: &str, trial: usize,
            fanout: usize, size_kib: usize, prefix: &str, id: &str, opts: &CaseOpts) {
    let base = scoped(prefix, id, trial, case);
    let init_ok = call(report, backend, "init", &format!("{case}/init"), trial, fanout, &base, size_kib, 0);
    if init_ok {
        match case {
            "handoff" => {
                if call(report, backend, "write", "handoff/write-fsync", trial, 1, &base, size_kib, 0) {
                    call(report, backend, "read", "handoff/read-fresh-vm", trial, 1, &base, size_kib, 0);
                }
            }
            "sqlite" => {
                if call(report, backend, "sqlite-write", "sqlite/commit-16", trial, 1, &base, size_kib, 0) {
                    call(report, backend, "sqlite-check", "sqlite/reopen-fresh-vm", trial, 1, &base, size_kib, 0);
                }
            }
            "readfan" => {
                if call(report, backend, "write", "readfan/prepare", trial, 1, &base, size_kib, 0) {
                    let api = Arc::new(backend.clone());
                    let mut handles = Vec::with_capacity(fanout);
                    for idx in 0..fanout {
                        let api = Arc::clone(&api);
                        let base = base.clone();
                        handles.push(std::thread::spawn(move || observe(&api, "read", "readfan/parallel-read", trial, fanout, &base, size_kib, idx, 0)));
                    }
                    for h in handles {
                        match h.join() {
                            Ok(ob) => { add(report, ob); }
                            Err(_) => { add(report, panic_observation("readfan/parallel-read", trial, fanout)); }
                        }
                    }
                }
            }
            "writers" => {
                let api = Arc::new(backend.clone());
                let mut handles = Vec::with_capacity(fanout);
                for idx in 0..fanout {
                    let api = Arc::clone(&api);
                    let base = base.clone();
                    handles.push(std::thread::spawn(move || observe(&api, "parallel-write", "writers/disjoint-path", trial, fanout, &base, size_kib, idx, 0)));
                }
                let mut all_ok = true;
                for h in handles {
                    match h.join() {
                        Ok(ob) => { all_ok &= add(report, ob); }
                        Err(_) => { add(report, panic_observation("writers/disjoint-path", trial, fanout)); all_ok = false; }
                    }
                }
                // Always attempt reconciliation, including after a writer failure:
                // the final read can expose which files are absent or inconsistent.
                call(report, backend, "parallel-check", "writers/reconcile", trial, fanout, &base, size_kib, 0);
                if !all_ok { eprintln!("One or more independent writers failed; see raw results"); }
            }
            "semantics" => {
                // Exec 2 creates entries; exec 3 and 4 (fresh microVMs) try to change them,
                // without and with `archil checkout` of the parent directory.
                if call(report, backend, "sem-setup", "semantics/setup", trial, 1, &base, size_kib, 0) {
                    call(report, backend, "sem-probe", "semantics/probe-plain", trial, 1, &base, size_kib, 0);
                    call(report, backend, "sem-probe-checkout", "semantics/probe-checkout", trial, 1, &base, size_kib, 0);
                }
            }
            "visibility" => {
                if call(report, backend, "vis-setup", "visibility/setup", trial, 1, &base, size_kib, 0) {
                    // The reader primes its caches, then polls; the writer starts after a delay.
                    let api = Arc::new(backend.clone());
                    let (rb, cap) = (base.clone(), opts.cap_secs);
                    let reader = std::thread::spawn(move ||
                        observe(&api, "vis-reader", "visibility/reader", trial, 2, &rb, size_kib, 0, cap));
                    std::thread::sleep(Duration::from_secs(opts.writer_delay_secs));
                    let writer = observe(backend, "vis-writer", "visibility/writer", trial, 2, &base, size_kib, 0, 0);
                    let mut reader = reader.join().unwrap_or_else(|_| panic_observation("visibility/reader", trial, 2));
                    check_visibility_order(&mut reader, &writer);
                    add(report, writer);
                    add(report, reader);
                }
            }
            _ => unreachable!("cases are validated in run()"),
        }
    }
    // Cleanup even when a preceding stage fails. Only the scoped directory is removed.
    call(report, backend, "cleanup", &format!("{case}/cleanup"), trial, fanout, &base, size_kib, 0);
}

/// A trial only measures staleness if the reader had primed its caches before the writer began.
fn check_visibility_order(reader: &mut Observation, writer: &Observation) {
    let primed = reader.detail.as_ref().and_then(|d| d["t_prime"].as_f64());
    let started = writer.detail.as_ref().and_then(|d| d["t_start"].as_f64());
    if let (Some(p), Some(w)) = (primed, started) {
        if p >= w {
            reader.ok = false;
            reader.error = Some(format!("Invalid trial: reader primed {:.2} s after the writer started; raise --writer-delay-secs", p - w));
        }
    } else if reader.ok {
        reader.ok = false;
        reader.error = Some("Invalid trial: missing reader or writer timestamps".into());
    }
}

fn calls_for(case: &str, fanout: usize) -> usize {
    match case {
        "handoff" | "sqlite" => 4,
        "readfan" | "writers" => 3 + fanout,
        "semantics" | "visibility" => 5,
        _ => 0,
    }
}

#[allow(clippy::too_many_arguments)]
fn run(use_archil: bool, runs: usize, size_kib: usize, fanout: usize,
       dir: &Path, output: &Path, opts: &CaseOpts) -> Result<(), String> {
    if !(1..=10).contains(&runs) { return Err("runs must be 1..10".into()); }
    if !(1..=4096).contains(&size_kib) { return Err("size-kib must be 1..4096".into()); }
    if !(1..=4).contains(&fanout) { return Err("fanout must be 1..4".into()); }
    if !(5..=280).contains(&opts.cap_secs) { return Err("cap-secs must be 5..280 (Archil's exec limit is 300 s)".into()); }
    if let Some(bad) = opts.cases.iter().find(|c| !CASES.contains(&c.as_str())) {
        return Err(format!("unknown case {bad:?}; expected one of {}", CASES.join(", ")));
    }
    let upper_bound: usize = runs * opts.cases.iter().map(|c| calls_for(c, fanout)).sum::<usize>();
    if upper_bound > 200 { return Err(format!("Refusing estimated {upper_bound} calls (> 200); lower runs or fanout")); }

    let (backend, region, suffix) = new_backend(use_archil)?;
    let prefix = if use_archil {
        "/mnt/archil/.diskscope".to_string()
    } else {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        let p = std::fs::canonicalize(dir).map_err(|e| e.to_string())?;
        p.join(".diskscope").to_string_lossy().to_string()
    };
    let id = unique_run_id();
    let backend_name = if use_archil { "archil-state" } else { "local-state" };
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    let mut report = RunReport {
        schema_version: 1,
        backend: backend_name.into(),
        run_id: id.clone(),
        timestamp_unix_secs: now,
        environment: Environment {
            region,
            disk_id_suffix: suffix,
            host_os: std::env::consts::OS.into(),
            workload_language: "Python 3 stdlib guest, Rust coordinator".into(),
            image: use_archil.then(|| IMAGE.to_owned()),
            size_mib: size_kib.div_ceil(1024),
            runs_per_task: runs,
            notes: "BRANCHLAB V0.3. Each step is a fresh process (local) or a fresh Archil microVM (default image, python3 -S). No host crash or lost-AZ test. Local and Archil have different hardware. handoff/sqlite/readfan/writers test FRESH-EXECUTION visibility, not a long-lived client's caches; the visibility case tests those with one long-lived reader exec. Archil commits a writing exec at exit, so a fresh-VM read after write-fsync cannot separate fsync durability from that exit commit. Independent writers never share a file. SQLite in sqlite/* uses journal_mode=TRUNCATE; semantics/* records what an exec can do to entries another exec created, with and without `archil checkout`. Cleanup runs with a conditional mount via POST /api/exec. No branches/checkpoints are created.".into(),
        },
        observations: Vec::new(),
    };

    println!("BranchLab {backend_name}: <= {upper_bound} calls; {runs} trial(s); {size_kib} KiB files; fanout {fanout}");
    if opts.cases.iter().any(|c| c == "visibility") {
        println!("visibility: each trial holds one reader exec open for up to {} s", opts.cap_secs);
    }
    for trial in 0..runs {
        for case in &opts.cases {
            run_case(&mut report, &backend, case, trial, fanout, size_kib, &prefix, &id, opts);
        }
    }
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let serialized = serde_json::to_vec_pretty(&report).map_err(|e| e.to_string())?;
    std::fs::write(output, serialized).map_err(|e| e.to_string())?;
    let fails = report.observations.iter().filter(|o| !o.ok).count();
    println!("Saved {} records to {} ({fails} failures)", report.observations.len(), output.display());
    if fails > 0 { Err(format!("{fails} phase(s) failed: review JSON; cleanup phases were attempted")) } else { Ok(()) }
}

fn main() {
    let cli = Cli::parse();
    let result = match cli.command {
        SubCmd::Local { runs, size_kib, fanout, dir, output, opts } => run(false, runs, size_kib, fanout, &dir, &output, &opts),
        SubCmd::Archil { runs, size_kib, fanout, output, opts } => run(true, runs, size_kib, fanout, Path::new("."), &output, &opts),
    };
    if let Err(e) = result {
        eprintln!("BranchLab error: {e}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_expected_regions() {
        assert!(endpoint("aws-us-west-2").is_ok());
        assert!(endpoint("aws-us-east-1").is_ok());
        assert!(endpoint("https://not-archil.test").is_err());
    }
    #[test]
    fn scoped_paths() {
        let p = scoped("/mnt/archil/.diskscope", "123-abcdefghi", 2, "sqlite");
        assert!(p.starts_with("/mnt/archil/.diskscope/branchlab-"));
        assert!(p.ends_with("-2-sqlite"));
    }
    #[test]
    fn quoting() {
        assert_eq!(sh_quote("a'b"), "'a'\\''b'");
    }
    #[test]
    fn tail_keeps_the_last_line() {
        assert_eq!(tail_chars("Traceback\nOSError: [Errno 30] Read-only file system", 34), "OSError: [Errno 30] Read-only file system".chars().skip(7).collect::<String>());
        assert_eq!(tail_chars("ab", 5), "ab");
    }
}
