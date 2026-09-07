// Tauri shell for AI Ecosystem.
//
// The shell hosts the React UI and supervises the local Python runtime:
// on startup it reuses a healthy backend on 127.0.0.1:8765 when one is
// already listening, otherwise it spawns `ai-ecosystem-serve` (falling
// back to `python -m ai_ecosystem.interface.serve` variants) as a child
// process, waits for the port to accept connections, and kills the
// child when the app exits. No tool execution, credentials, or policy
// logic lives here; the backend remains the only authority.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::OpenOptions;
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::State;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8765;
const BACKEND_URL: &str = "http://127.0.0.1:8765";
/// Total time spent trying backend candidates before giving up.
const STARTUP_BUDGET: Duration = Duration::from_secs(25);
/// Per-candidate wait for the port to open.
const CANDIDATE_TIMEOUT: Duration = Duration::from_secs(10);
const POLL_STEP: Duration = Duration::from_millis(250);

#[derive(Clone, Serialize)]
struct BackendStatus {
    /// "external" (already running), "managed" (spawned by us), "failed".
    state: &'static str,
    url: &'static str,
    detail: String,
}

struct Backend {
    child: Option<Child>,
    status: BackendStatus,
}

struct BackendState(Arc<Mutex<Backend>>);

#[tauri::command]
fn backend_status(state: State<BackendState>) -> BackendStatus {
    match state.0.lock() {
        Ok(backend) => backend.status.clone(),
        Err(_) => BackendStatus {
            state: "failed",
            url: BACKEND_URL,
            detail: "backend state lock poisoned".to_string(),
        },
    }
}

fn backend_addr() -> SocketAddr {
    format!("{}:{}", BACKEND_HOST, BACKEND_PORT)
        .parse()
        .expect("loopback backend address must parse")
}

fn port_open() -> bool {
    TcpStream::connect_timeout(&backend_addr(), Duration::from_millis(400)).is_ok()
}

fn wait_for_port(deadline: Instant) -> bool {
    while Instant::now() < deadline {
        if port_open() {
            return true;
        }
        std::thread::sleep(POLL_STEP);
    }
    port_open()
}

/// Per-user data dir: `%APPDATA%\AI Ecosystem` on Windows, else the
/// current directory. The backend DB, workspace, and log live here so
/// the app never writes next to its own binaries.
fn data_dir() -> PathBuf {
    let base = std::env::var("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| std::env::current_dir().unwrap_or(PathBuf::from(".")));
    base.join("AI Ecosystem")
}

fn pid_file(dir: &std::path::Path) -> PathBuf {
    dir.join("backend.pid")
}

fn read_pid(path: &std::path::Path) -> Option<u32> {
    std::fs::read_to_string(path)
        .ok()?
        .trim()
        .parse()
        .ok()
}

fn write_pid(path: &std::path::Path, pid: u32) {
    let _ = std::fs::write(path, pid.to_string());
}

fn clear_pid(path: &std::path::Path) {
    let _ = std::fs::remove_file(path);
}

/// PID of the process LISTENING on the backend port (Windows netstat).
/// None when nothing listens or the table cannot be read.
fn listener_pid() -> Option<u32> {
    let output = Command::new("netstat").args(["-ano"]).output().ok()?;
    let text = String::from_utf8_lossy(&output.stdout);
    let want = format!("{}:{}", BACKEND_HOST, BACKEND_PORT);
    for line in text.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() >= 5
            && parts[0] == "TCP"
            && parts[1] == want
            && parts[3] == "LISTENING"
        {
            if let Ok(pid) = parts[4].parse() {
                return Some(pid);
            }
        }
    }
    None
}

fn process_alive(pid: u32) -> bool {
    // Signal 0 equivalent: tasklist filtered by PID. Cheap and enough
    // to distinguish a live owner from a stale pid file.
    Command::new("tasklist")
        .args(["/FI", &format!("PID eq {}", pid), "/NH"])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains(&pid.to_string()))
        .unwrap_or(false)
}

/// One WMIC process field (ParentProcessId / CommandLine) for a PID.
fn wmic_field(pid: u32, field: &str) -> Option<String> {
    let output = Command::new("wmic")
        .args([
            "process",
            "where",
            &format!("ProcessId={}", pid),
            "get",
            field,
            "/value",
        ])
        .output()
        .ok()?;
    let text = String::from_utf8_lossy(&output.stdout);
    text.lines().find_map(|line| {
        let (key, value) = line.split_once('=')?;
        if key.trim().eq_ignore_ascii_case(field) {
            Some(value.trim().to_string())
        } else {
            None
        }
    })
}

fn parent_pid(pid: u32) -> Option<u32> {
    wmic_field(pid, "ParentProcessId")?.parse().ok()
}

fn is_own_backend_command(pid: u32) -> bool {
    wmic_field(pid, "CommandLine")
        .map(|cmd| {
            cmd.contains("ai_ecosystem.interface.serve")
                || cmd.contains("ai-ecosystem-serve")
        })
        .unwrap_or(false)
}

fn kill_tree(pid: u32) -> bool {
    terminate_tree(pid)
}

/// Stop a supervised backend tree: ask nicely first (lets the server
/// flush and close its database), then force. Plain `taskkill /T`
/// only delivers a close message, which console Python ignores --
/// without the force fallback the backend would orphan on every exit.
fn terminate_tree(pid: u32) -> bool {
    let arg = pid.to_string();
    let _ = Command::new("taskkill")
        .args(["/PID", &arg, "/T"])
        .output();
    let gentle = Instant::now() + Duration::from_secs(3);
    while port_open() && Instant::now() < gentle {
        std::thread::sleep(POLL_STEP);
    }
    if port_open() {
        let _ = Command::new("taskkill")
            .args(["/F", "/PID", &arg, "/T"])
            .output();
    }
    wait_port_closed();
    !port_open()
}

fn wait_port_closed() {
    let deadline = Instant::now() + Duration::from_secs(10);
    while port_open() && Instant::now() < deadline {
        std::thread::sleep(POLL_STEP);
    }
}

/// Reap one of our own orphaned backends (previous app killed/crashed
/// before it could clean up). Only ever touches processes that are
/// provably ours:
/// * the recorded PID itself listening on the port, or
/// * the recorded wrapper whose *child* listens (pip console-script
///   launchers re-spawn Python, so the binder is a grandchild), or
/// * a dead recorded PID with our module on the listener command line.
///
/// Anything else is a foreign server and is adopted as "external".
fn reap_own_orphan(dir: &std::path::Path) -> bool {
    let pid_path = pid_file(dir);
    let recorded = match read_pid(&pid_path) {
        Some(pid) => pid,
        None => return false,
    };
    let listener = match listener_pid() {
        Some(pid) => pid,
        None => {
            if !process_alive(recorded) {
                clear_pid(&pid_path); // stale file, nothing to reap
            }
            return false;
        }
    };
    let ours = listener == recorded
        || parent_pid(listener) == Some(recorded)
        || (!process_alive(recorded) && is_own_backend_command(listener));
    if !ours {
        return false;
    }
    // Kill the tree root we own: the wrapper when the binder is its
    // child (kills both), else the listener itself.
    let root = if parent_pid(listener) == Some(recorded) {
        recorded
    } else {
        listener
    };
    if kill_tree(root) {
        wait_port_closed();
    }
    clear_pid(&pid_path);
    !port_open()
}

fn launch_candidate(
    program: &str,
    extra_args: &[String],
    log_path: &std::path::Path,
) -> Option<Child> {
    let out_log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)
        .ok()?;
    let err_log = out_log.try_clone().ok()?;
    let mut cmd = Command::new(program);
    #[cfg(windows)]
    cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW: no console popup
    cmd.args(extra_args)
        .stdin(Stdio::null())
        .stdout(Stdio::from(out_log))
        .stderr(Stdio::from(err_log));
    cmd.spawn().ok()
}

fn ensure_backend() -> Backend {
    let dir = data_dir();
    // Crash recovery first: a previous app may have died without
    // killing its child; reclaim the port only from our own orphan.
    reap_own_orphan(&dir);
    if port_open() {
        return Backend {
            child: None,
            status: BackendStatus {
                state: "external",
                url: BACKEND_URL,
                detail: format!("reusing backend already listening on {}", BACKEND_URL),
            },
        };
    }

    let db_path = dir.join("runtime.db");
    let workspace = dir.join("workspace");
    let log_path = dir.join("backend.log");
    if let Err(err) = std::fs::create_dir_all(&workspace) {
        return Backend {
            child: None,
            status: BackendStatus {
                state: "failed",
                url: BACKEND_URL,
                detail: format!("cannot create data dir {}: {}", dir.display(), err),
            },
        };
    }
    let fixed_args = vec![
        "--db".to_string(),
        db_path.to_string_lossy().into_owned(),
        "--workspace".to_string(),
        workspace.to_string_lossy().into_owned(),
    ];

    // Direct `python -m` spawns first, console script last: pip and
    // Store launchers re-spawn Python as a grandchild, so a direct
    // spawn keeps the recorded PID closest to the port binder (see
    // reap_own_orphan). Each candidate is health-checked: a spawn
    // that never opens the port is tree-killed before trying the next.
    let candidates: Vec<(String, Vec<String>)> = vec![
        (
            "python".to_string(),
            [&["-m".to_string(), "ai_ecosystem.interface.serve".to_string()][..], &fixed_args[..]].concat(),
        ),
        (
            "py".to_string(),
            [&["-3".to_string(), "-m".to_string(), "ai_ecosystem.interface.serve".to_string()][..], &fixed_args[..]]
                .concat(),
        ),
        (
            "python3".to_string(),
            [&["-m".to_string(), "ai_ecosystem.interface.serve".to_string()][..], &fixed_args[..]].concat(),
        ),
        ("ai-ecosystem-serve".to_string(), fixed_args.clone()),
    ];

    let started = Instant::now();
    let mut tried: Vec<String> = Vec::new();
    for (program, args) in &candidates {
        if started.elapsed() >= STARTUP_BUDGET {
            break;
        }
        tried.push(program.clone());
        let deadline = std::cmp::min(
            started + STARTUP_BUDGET,
            Instant::now() + CANDIDATE_TIMEOUT,
        );
        match launch_candidate(program, args, &log_path) {
            Some(child) => {
                if wait_for_port(deadline) {
                    let pid = child.id();
                    write_pid(&pid_file(&dir), pid);
                    return Backend {
                        child: Some(child),
                        status: BackendStatus {
                            state: "managed",
                            url: BACKEND_URL,
                            detail: format!(
                                "started '{}' (pid {}); logs at {}",
                                program,
                                pid,
                                log_path.display()
                            ),
                        },
                    };
                }
                // Tree-kill (see kill_child): a half-started candidate
                // may have re-spawned a grandchild that would bind late.
                let pid = child.id();
                drop(child);
                kill_tree(pid);
                wait_port_closed();
            }
            None => continue, // not installed / cannot spawn: try next
        }
    }
    Backend {
        child: None,
        status: BackendStatus {
            state: "failed",
            url: BACKEND_URL,
            detail: format!(
                "could not start a Python backend (tried: {}). Install it with 'pip install -e .' and start 'ai-ecosystem-serve' manually.",
                tried.join(", ")
            ),
        },
    }
}

fn kill_child(state: &Arc<Mutex<Backend>>) {
    // Tree-kill, not Child::kill: interpreter shims (Windows Store
    // Python, pip console scripts) re-spawn the real backend as a
    // grandchild, and killing only the direct child would orphan it
    // on every app exit.
    let pid = match state.lock() {
        Ok(mut backend) => backend.child.take().map(|child| child.id()),
        Err(_) => None,
    };
    if let Some(pid) = pid {
        terminate_tree(pid);
    }
    clear_pid(&pid_file(&data_dir()));
}

fn main() {
    let backend = ensure_backend();
    if let Some(ref child) = backend.child {
        eprintln!("AI Ecosystem backend: {}", child.id());
    } else {
        eprintln!(
            "AI Ecosystem backend: {} ({})",
            backend.status.state, backend.status.detail
        );
    }
    let shared = Arc::new(Mutex::new(backend));
    let for_window = shared.clone();
    let for_exit = shared.clone();

    tauri::Builder::default()
        .manage(BackendState(shared))
        .invoke_handler(tauri::generate_handler![backend_status])
        .setup(|app| {
            // Keep a handle so windows can resolve app state normally.
            let _ = app.handle();
            Ok(())
        })
        .on_window_event(move |_window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                kill_child(&for_window);
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run AI Ecosystem desktop shell");
    kill_child(&for_exit);
}
