use crate::model::RunReport;
use std::path::{Path, PathBuf};

const HTML_TEMPLATE: &str = include_str!("../dashboard/template.html");
const THEME_CSS: &str = include_str!("../dashboard/chart-theme.css");
const PAGE_CSS: &str = include_str!("../dashboard/style.css");
const CHARTS_JS: &str = include_str!("../dashboard/charts.js");
const VIEWS_JS: &str = include_str!("../dashboard/views.js");
const APP_JS: &str = include_str!("../dashboard/app.js");

pub fn write(inputs: &[PathBuf], output: &Path) -> Result<(), String> {
    let mut reports = Vec::<RunReport>::new();
    for input in inputs {
        let bytes = std::fs::read(input).map_err(|e| format!("{}: {e}", input.display()))?;
        let report: RunReport = serde_json::from_slice(&bytes).map_err(|e| format!("{}: {e}", input.display()))?;
        if report.schema_version != 1 { return Err("Unsupported JSON schema version".into()); }
        reports.push(report);
    }
    let safe_json = serde_json::to_string(&reports).map_err(|e| e.to_string())?
        .replace('<', "\\u003c")
        .replace('>', "\\u003e")
        .replace('&', "\\u0026");
    let css = format!("{THEME_CSS}\n{PAGE_CSS}");
    let js = format!("{CHARTS_JS}\n{VIEWS_JS}\n{APP_JS}");
    // Code first, data last: measured strings (e.g. error text) can never be rewritten by a later
    // substitution. The bundles are checked by a unit test not to contain the data placeholder.
    let html = HTML_TEMPLATE
        .replace("/*__STYLE__*/", &css)
        .replace("/*__SCRIPT__*/", &js)
        .replace("__REPORT_DATA__", &safe_json);
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    std::fs::write(output, html).map_err(|e| format!("{}: {e}", output.display()))?;
    println!("Generated offline dashboard: {}", output.display());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bundles_do_not_contain_data_placeholder() {
        for src in [THEME_CSS, PAGE_CSS, CHARTS_JS, VIEWS_JS, APP_JS] {
            assert!(!src.contains("__REPORT_DATA__"));
        }
        assert_eq!(HTML_TEMPLATE.matches("__REPORT_DATA__").count(), 1);
    }
}
