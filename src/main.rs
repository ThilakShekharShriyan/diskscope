mod archil;
mod model;
mod report;

use clap::{Parser, Subcommand};
use model::{shuffle, tail_chars, Observation, RunReport, TASKS, WORKER_SCRIPT};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

#[derive(Parser)]
#[command(name = "diskscope", version, about = "Safe Archil serverless and filesystem benchmark lab")]
struct Cli {
    #[command(subcommand)]
    command: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Benchmark host filesystem with identical Python 3 guest workload.
    Local {
        #[arg(long, default_value = ".diskscope-work")]
        dir: PathBuf,
        #[arg(long, default_value_t = 3)]
        runs: usize,
        #[arg(long, default_value_t = 4)]
        size_mib: usize,
        #[arg(long, default_value = "results/local.json")]
        output: PathBuf,
    },
    /// Benchmark an EXISTING Archil disk. Does not create/delete disks.
    Archil {
        #[arg(long, default_value_t = 3)]
        runs: usize,
        #[arg(long, default_value_t = 4)]
        size_mib: usize,
        /// Comma-separated concurrency levels. Capped at 8.
        #[arg(long, default_value = "1,2,4")]
        fanout: String,
        /// Run up to N suite calls at once (each call is its own microVM). 1 = strictly sequential.
        #[arg(long, default_value_t = 1)]
        parallel: usize,
        /// `default` uses Archil's pre-warmed VM pool; an OCI reference (e.g. python:3.13-slim) boots on demand.
        #[arg(long, default_value = archil::DEFAULT_IMAGE)]
        image: String,
        #[arg(long, default_value = "results/archil.json")]
        output: PathBuf,
    },
    /// Generate a self-contained offline HTML dashboard (commit to docs/index.html).
    Report {
        #[arg(long, required = true, num_args = 1..)]
        input: Vec<PathBuf>,
        #[arg(long, default_value = "docs/index.html")]
        output: PathBuf,
    },
    /// A Rust-only hot CPU workload for REAL sampling with cargo-flamegraph.
    ProfileWorkload {
        #[arg(long, default_value_t = 150_000_000)]
        iterations: u64,
    },
}

fn validate(runs: usize, size_mib: usize) -> Result<(), String> {
    if !(1..=20).contains(&runs) { return Err("runs must be between 1 and 20; start small".into()); }
    if !(1..=64).contains(&size_mib) { return Err("size-mib must be between 1 and 64".into()); }
    Ok(())
}

fn save_json(path: &Path, report: &RunReport) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("Creating {}: {e}", parent.display()))?;
    }
    let serialized = serde_json::to_vec_pretty(report).map_err(|e| e.to_string())?;
    std::fs::write(path, serialized).map_err(|e| format!("Writing {}: {e}", path.display()))?;
    println!("Saved {} observations -> {}", report.observations.len(), path.display());
    Ok(())
}

fn run_local(root: PathBuf, runs: usize, size_mib: usize) -> Result<RunReport, String> {
    let root = if root.is_absolute() { root } else {
        std::env::current_dir().map_err(|e| e.to_string())?.join(root)
    };
    std::fs::create_dir_all(&root).map_err(|e| e.to_string())?;
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
    let scope = format!("{}-{}", std::process::id(), now.as_secs());
    let seed = now.as_nanos() as u64 ^ std::process::id() as u64;
    let mut report = RunReport::empty("local", scope.clone(), size_mib, runs);
    report.environment.shuffle_seed = Some(seed);
    report.environment.variant = Some("local SSD · durable fsync".into());
    report.environment.notes = "Local baseline: a fresh `python3 -S` process per trial running the same guest source as Archil, on this host's disk with durable flushes (F_FULLFSYNC on macOS). Client wall-clock includes interpreter start-up, so compare guest work_ms across environments.".into();
    let scope_dir = root.join(".diskscope").join(&scope);
    let mut jobs: Vec<(&str, usize)> = TASKS.iter().flat_map(|&t| (0..runs).map(move |i| (t, i))).collect();
    shuffle(&mut jobs, seed);
    let run_start = Instant::now();
    for (task, trial) in jobs {
        let mut observation = Observation::new(task, trial, 1);
        observation.started_ms = Some(run_start.elapsed().as_secs_f64() * 1000.0);
        observation.parallel = Some(1);
        let start = Instant::now();
        let child = Command::new("python3")
            .arg("-S").arg("-c").arg(WORKER_SCRIPT)
            .arg(task).arg(scope_dir.join(format!("{task}-{trial}-1"))).arg(size_mib.to_string())
            .output();
        observation.client_wall_ms = start.elapsed().as_secs_f64() * 1000.0;
        match child {
            Err(e) => observation.error = Some(format!("Could not invoke Python 3: {e}")),
            Ok(child) => {
                let parsed = observation.apply_guest_output(&String::from_utf8_lossy(&child.stdout));
                if !child.status.success() {
                    observation.ok = false;
                    observation.error = Some(tail_chars(&String::from_utf8_lossy(&child.stderr), 600));
                } else if !parsed {
                    observation.error = Some("Missing or undecodable DISKSCOPE_JSON line".into());
                }
            }
        }
        println!("{task:<16} local trial {:>2}: {:>8.2} ms  {}", trial + 1, observation.client_wall_ms, if observation.ok {"OK"} else {"FAIL"});
        report.observations.push(observation);
    }
    // The guest removes everything it creates; tidy the (now empty) shared files directory. A failure
    // here is recorded, never allowed to discard the measurements.
    let files_dir = root.join(".diskscope").join("files");
    if files_dir.exists() {
        if let Err(e) = std::fs::remove_dir(&files_dir) {
            report.environment.notes.push_str(&format!(" Cleanup warning: could not remove {}: {e}", files_dir.display()));
        }
    }
    Ok(report)
}

#[inline(never)]
fn avalanche(mut x: u64) -> u64 {
    x ^= x >> 30;
    x = x.wrapping_mul(0xbf58476d1ce4e5b9);
    x ^= x >> 27;
    x = x.wrapping_mul(0x94d049bb133111eb);
    x ^ (x >> 31)
}

#[inline(never)]
fn mix_accumulator(state: u64, step: u64) -> u64 {
    avalanche(state.wrapping_add(step).rotate_left(13))
}

/// Phase 1: integer mixing (SplitMix64 finalizer, the same mixing `model::shuffle` uses).
#[inline(never)]
fn phase_mix(iterations: u64) -> u64 {
    let mut acc = 0xDEADBEEFu64;
    for step in 0..iterations {
        acc = mix_accumulator(std::hint::black_box(acc), step);
    }
    acc
}

/// Phase 2: the statistics path — sort latency samples and take quantiles.
#[inline(never)]
fn phase_quantiles(rounds: u64) -> f64 {
    let mut total = 0.0;
    let mut samples = vec![0.0f64; 4096];
    for r in 0..rounds {
        for (i, v) in samples.iter_mut().enumerate() {
            *v = (avalanche(r ^ i as u64) % 1_000_000) as f64 / 1000.0;
        }
        for p in [0.5, 0.9, 0.95, 0.99] {
            total += model::percentile(std::hint::black_box(&samples), p).unwrap_or(0.0);
        }
    }
    total
}

/// Phase 3: the report path — serialize and parse a RunReport the size of a real run.
#[inline(never)]
fn phase_json(rounds: u64) -> usize {
    let mut report = RunReport::empty("archil", "profile".into(), 4, 10);
    for i in 0..256u64 {
        report.observations.push(Observation {
            task: TASKS[(i % TASKS.len() as u64) as usize].into(), trial: i as usize, fanout: 1, ok: true,
            client_wall_ms: (avalanche(i) % 1000) as f64, remote_total_ms: Some(250.0), remote_queue_ms: Some(140.0),
            remote_execute_ms: Some(110.0), work_ms: Some(1.5), operations: Some(1), bytes: Some(4096), error: None,
            cleanup_error: None, started_ms: Some(i as f64), parallel: Some(4),
        });
    }
    let mut bytes = 0;
    for _ in 0..rounds {
        let text = serde_json::to_string(std::hint::black_box(&report)).unwrap_or_default();
        let back: RunReport = serde_json::from_str(&text).expect("round-trip");
        bytes += text.len() + back.observations.len();
    }
    bytes
}

/// Phase 4: the scheduler path — deterministic shuffles of a job list.
#[inline(never)]
fn phase_shuffle(rounds: u64) -> usize {
    let mut jobs: Vec<(usize, usize)> = (0..TASKS.len()).flat_map(|t| (0..64).map(move |i| (t, i))).collect();
    let mut checksum = 0;
    for r in 0..rounds {
        model::shuffle(&mut jobs, r);
        checksum ^= std::hint::black_box(jobs[0].0 + jobs[1].1);
    }
    checksum
}

/// Synthetic CPU-only workload with distinct, non-inlined phases built from the harness's own
/// building blocks (mixing, quantiles, report serde, shuffling), so a sampled flamegraph has readable
/// stacks. It is a profiling target, not a measurement of a real benchmark run.
fn profile_workload(iterations: u64) {
    let started = Instant::now();
    let scale = iterations / 250_000;
    let mix = phase_mix(iterations);
    let q = phase_quantiles(scale * 2);
    let json = phase_json(scale / 2);
    let shuf = phase_shuffle(scale * 160);
    println!(
        "Rust profile workload: iterations={iterations} checksum={:x} elapsed={:.3}s",
        mix ^ q.to_bits() ^ json as u64 ^ shuf as u64,
        started.elapsed().as_secs_f64()
    );
}

fn main() {
    let cli = Cli::parse();
    let result = match cli.command {
        Cmd::Local { dir, runs, size_mib, output } => {
            validate(runs, size_mib).and_then(|()| run_local(dir, runs, size_mib)).and_then(|r| {
                let failed = r.observations.iter().filter(|o| !o.ok).count();
                save_json(&output, &r)?;
                if failed > 0 { Err(format!("{failed} task(s) failed; inspect JSON")) } else { Ok(()) }
            })
        }
        Cmd::Archil { runs, size_mib, fanout, parallel, image, output } => {
            (|| {
                validate(runs, size_mib)?;
                let levels: Result<Vec<usize>, _> = fanout.split(',').map(|x| x.trim().parse::<usize>()).collect();
                let levels = levels.map_err(|_| "fanout must be comma-separated positive integers".to_owned())?;
                if levels.is_empty() || levels.iter().any(|x| *x == 0 || *x > 8) {
                    return Err("fanout levels must be between 1 and 8".into());
                }
                if !(1..=8).contains(&parallel) { return Err("parallel must be between 1 and 8".into()); }
                let warmups = parallel.max(levels.iter().copied().max().unwrap_or(1));
                let calls = 1 + warmups + TASKS.len() * runs + levels.iter().sum::<usize>() * runs;
                if calls > 200 { return Err(format!("Refusing {calls} calls: maximum is 200 per command")); }
                println!("Planned remote calls: {calls} ({warmups} warm-up, 1 cleanup), up to {parallel} in flight, image={image}. Does NOT create/delete a disk.");
                let report = archil::run(runs, size_mib, &levels, parallel, &image)?;
                let failed = report.observations.iter().filter(|o| !o.ok).count();
                save_json(&output, &report)?;
                if failed > 0 { Err(format!("{failed} task(s) failed; inspect JSON")) } else { Ok(()) }
            })()
        }
        Cmd::Report { input, output } => report::write(&input, &output),
        Cmd::ProfileWorkload { iterations } => {
            if iterations == 0 || iterations > 2_000_000_000 {
                Err("iterations must be 1..=2,000,000,000".into())
            } else { profile_workload(iterations); Ok(()) }
        }
    };
    if let Err(err) = result {
        eprintln!("diskscope: {err}");
        std::process::exit(1);
    }
}
