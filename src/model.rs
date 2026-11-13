use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Observation {
    pub task: String,
    pub trial: usize,
    pub fanout: usize,
    pub ok: bool,
    pub client_wall_ms: f64,
    pub remote_total_ms: Option<f64>,
    pub remote_queue_ms: Option<f64>,
    pub remote_execute_ms: Option<f64>,
    pub work_ms: Option<f64>,
    pub operations: Option<u64>,
    pub bytes: Option<u64>,
    pub error: Option<String>,
    /// Guest could not remove its scoped scratch directory. The measurement still counts;
    /// this is reported separately so cleanup problems stay visible.
    #[serde(default)]
    pub cleanup_error: Option<String>,
    /// Milliseconds from the start of the run until this call was submitted.
    #[serde(default)]
    pub started_ms: Option<f64>,
    /// How many calls were allowed in flight at once when this observation was taken.
    #[serde(default)]
    pub parallel: Option<usize>,
}

/// Keep the end of guest stderr: Python puts the actual exception on the last lines.
pub fn tail_chars(s: &str, n: usize) -> String {
    let count = s.chars().count();
    s.chars().skip(count.saturating_sub(n)).collect()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunReport {
    pub schema_version: u32,
    pub backend: String,
    pub run_id: String,
    pub timestamp_unix_secs: u64,
    pub environment: Environment,
    pub observations: Vec<Observation>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Environment {
    pub region: Option<String>,
    pub disk_id_suffix: Option<String>,
    pub host_os: String,
    pub workload_language: String,
    pub image: Option<String>,
    pub size_mib: usize,
    pub runs_per_task: usize,
    pub notes: String,
    #[serde(default)]
    pub parallel: Option<usize>,
    #[serde(default)]
    pub diskscope_version: Option<String>,
    /// Seed of the shuffled (task, trial) order, when the run used one. Replays the exact order.
    #[serde(default)]
    pub shuffle_seed: Option<u64>,
}

impl RunReport {
    pub fn empty(backend: &str, run_id: String, size_mib: usize, runs: usize) -> Self {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        Self {
            schema_version: 1,
            backend: backend.to_owned(),
            run_id,
            timestamp_unix_secs: now,
            environment: Environment {
                region: None,
                disk_id_suffix: None,
                host_os: std::env::consts::OS.to_owned(),
                workload_language: "Python 3 standard library (guest)".into(),
                image: None,
                size_mib,
                runs_per_task: runs,
                notes: "Guest duration excludes the individual test's fixture setup/cleanup where specified".into(),
                parallel: None,
                diskscope_version: Some(env!("CARGO_PKG_VERSION").to_owned()),
                shuffle_seed: None,
            },
            observations: Vec::new(),
        }
    }
}

pub const TASKS: &[&str] = &[
    "noop", "stat", "small-files", "fsync-4k", "seq-write", "seq-read", "sqlite-commit", "hash-64m",
];

/// Deterministic Fisher-Yates shuffle (SplitMix64), so a run's task order is reproducible from its seed.
pub fn shuffle<T>(items: &mut [T], seed: u64) {
    let mut state = seed;
    let mut next = || {
        state = state.wrapping_add(0x9E3779B97F4A7C15);
        let mut z = state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D049BB133111EB);
        z ^ (z >> 31)
    };
    for i in (1..items.len()).rev() {
        let j = (next() % (i as u64 + 1)) as usize;
        items.swap(i, j);
    }
}

#[cfg_attr(not(test), allow(dead_code))] // used by tests; dashboard computes its own quantiles in JS
pub fn percentile(values: &[f64], p: f64) -> Option<f64> {
    if values.is_empty() { return None; }
    let mut xs = values.to_vec();
    xs.sort_by(|a,b| a.total_cmp(b));
    let position = (xs.len() - 1) as f64 * p;
    let low = position.floor() as usize;
    let high = position.ceil() as usize;
    Some(xs[low] + (xs[high] - xs[low]) * (position - low as f64))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_percentile() {
        assert_eq!(percentile(&[], 0.5), None);
        assert_eq!(percentile(&[5.], 0.95), Some(5.));
        assert_eq!(percentile(&[3.,1.,2.], 0.5), Some(2.));
        assert_eq!(percentile(&[10.,0.], 0.5), Some(5.));
    }
    #[test]
    fn shuffle_is_deterministic_permutation() {
        let mut a: Vec<u32> = (0..50).collect();
        let mut b = a.clone();
        shuffle(&mut a, 7);
        shuffle(&mut b, 7);
        assert_eq!(a, b);
        assert_ne!(a, (0..50).collect::<Vec<_>>());
        a.sort();
        assert_eq!(a, (0..50).collect::<Vec<_>>());
    }
    #[test]
    fn shuffle_seed_round_trips_and_old_json_still_loads() {
        let mut r = RunReport::empty("archil", "x".into(), 1, 1);
        r.environment.shuffle_seed = Some(u64::MAX - 7);
        let back: RunReport = serde_json::from_str(&serde_json::to_string(&r).unwrap()).unwrap();
        assert_eq!(back.environment.shuffle_seed, Some(u64::MAX - 7));
        // Reports written before these fields existed must keep loading.
        let old = r#"{"schema_version":1,"backend":"local","run_id":"a","timestamp_unix_secs":1,
          "environment":{"region":null,"disk_id_suffix":null,"host_os":"macos","workload_language":"py",
          "image":null,"size_mib":1,"runs_per_task":1,"notes":""},"observations":[]}"#;
        let parsed: RunReport = serde_json::from_str(old).unwrap();
        assert_eq!(parsed.environment.shuffle_seed, None);
    }
    #[test]
    fn tail_keeps_end() {
        assert_eq!(tail_chars("abcdef", 3), "def");
        assert_eq!(tail_chars("ab", 5), "ab");
    }
}
