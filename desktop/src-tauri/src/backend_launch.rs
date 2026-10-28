use std::path::Path;

/// Prefer the source checkout's environment; GUI launches need not inherit an
/// activated environment or a PATH containing a system-wide Python install.
pub fn candidates(root: Option<&Path>, fixed_args: &[String]) -> Vec<(String, Vec<String>)> {
    let module_args: Vec<String> = [
        &["-m".to_string(), "ai_ecosystem.interface.serve".to_string()][..],
        fixed_args,
    ].concat();
    let mut result = Vec::new();
    if let Some(root) = root {
        let relative = if cfg!(windows) { ".venv/Scripts/python.exe" } else { ".venv/bin/python" };
        let python = root.join(relative);
        if python.is_file() {
            result.push((python.to_string_lossy().into_owned(), module_args.clone()));
        }
    }
    result.push(("python".to_string(), module_args.clone()));
    result.push(("py".to_string(), [&["-3".to_string()][..], &module_args].concat()));
    result.push(("python3".to_string(), module_args));
    result.push(("ai-ecosystem-serve".to_string(), fixed_args.to_vec()));
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checkout_environment_precedes_system_python_and_keeps_arguments() {
        let root = std::env::temp_dir().join(format!("raphael-launch-{}", std::process::id()));
        let relative = if cfg!(windows) { ".venv/Scripts/python.exe" } else { ".venv/bin/python" };
        let python = root.join(relative);
        std::fs::create_dir_all(python.parent().unwrap()).unwrap();
        std::fs::write(&python, []).unwrap();
        let args = vec!["--db".into(), "path with spaces/runtime.db".into()];
        let found = candidates(Some(&root), &args);
        assert_eq!(Path::new(&found[0].0), python);
        assert_eq!(&found[0].1[2..], args.as_slice());
        assert_eq!(&found[0].1[..2], ["-m", "ai_ecosystem.interface.serve"]);
        assert_eq!(found[1].0, "python");
        std::fs::remove_file(python).unwrap();
    }

    #[test]
    fn no_checkout_preserves_system_fallbacks() {
        let found = candidates(None, &[]);
        assert_eq!(found.iter().map(|item| item.0.as_str()).collect::<Vec<_>>(),
                   ["python", "py", "python3", "ai-ecosystem-serve"]);
        assert_eq!(&found[1].1[..2], ["-3", "-m"]);
        assert!(found[3].1.is_empty());
    }

    #[test]
    fn missing_checkout_environment_still_tries_system_python() {
        assert_eq!(candidates(Some(Path::new("nonexistent-checkout")), &[])[0].0, "python");
    }
}
