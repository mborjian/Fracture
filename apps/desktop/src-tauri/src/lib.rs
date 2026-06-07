use once_cell::sync::Lazy;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs;
use std::io::{Read, Write};
use std::net::TcpStream;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use tauri::menu::{Menu, MenuItem};
use tauri::path::BaseDirectory;
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager};
#[cfg(windows)]
use winreg::enums::{HKEY_CURRENT_USER, KEY_READ, KEY_WRITE};
#[cfg(windows)]
use winreg::RegKey;

static BACKEND_CHILD: Lazy<Mutex<Option<Child>>> = Lazy::new(|| Mutex::new(None));
static BACKEND_SHUTTING_DOWN: Lazy<AtomicBool> = Lazy::new(|| AtomicBool::new(false));

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;
const TRAY_ID: &str = "main-tray";
const APP_NAME: &str = "Fracture";
const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8765;
const RUN_REG_PATH: &str = "Software\\Microsoft\\Windows\\CurrentVersion\\Run";
const RUN_VALUE_NAME: &str = APP_NAME;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct UiSettings {
    theme: String,
    update_channel: String,
    run_on_startup: bool,
    close_to_tray: bool,
}

impl Default for UiSettings {
    fn default() -> Self {
        Self {
            theme: "system".to_string(),
            update_channel: "stable".to_string(),
            run_on_startup: false,
            close_to_tray: true,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CoreSettings {
    proxy_scope: String,
    proxy_port: u16,
    socks_port: u16,
    auto_reconnect: bool,
}

impl Default for CoreSettings {
    fn default() -> Self {
        Self {
            proxy_scope: "local".to_string(),
            proxy_port: 2080,
            socks_port: 2081,
            auto_reconnect: true,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct AppSettings {
    #[serde(default)]
    ui: UiSettings,
    #[serde(default)]
    core: CoreSettings,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
struct RuntimeStatus {
    #[serde(default)]
    state: String,
    #[serde(default)]
    proxy_scope: String,
}

fn first_existing(paths: &[PathBuf]) -> Option<PathBuf> {
    paths.iter().find(|p| p.exists()).cloned()
}

fn bundled_backend_executable(app: &AppHandle) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(path) = app
        .path()
        .resolve("backend/fracture-backend.exe", BaseDirectory::Resource)
    {
        candidates.push(path);
    }
    if let Ok(path) = app
        .path()
        .resolve("fracture-backend.exe", BaseDirectory::Resource)
    {
        candidates.push(path);
    }
    if let Ok(path) = app
        .path()
        .resolve("backend/dist/fracture-backend.exe", BaseDirectory::Resource)
    {
        candidates.push(path);
    }
    if let Ok(path) = app
        .path()
        .resolve("backend/fracture-backend", BaseDirectory::Resource)
    {
        candidates.push(path);
    }
    if let Ok(path) = app
        .path()
        .resolve("fracture-backend", BaseDirectory::Resource)
    {
        candidates.push(path);
    }
    first_existing(&candidates)
}

fn local_release_backend_executable() -> Option<PathBuf> {
    let base = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("backend")
        .join("dist");
    first_existing(&[
        base.join("fracture-backend.exe"),
        base.join("fracture-backend"),
    ])
}

fn dev_backend_dir() -> Option<PathBuf> {
    let base = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("backend");
    if base.exists() {
        return Some(base);
    }
    None
}

fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("..")
}

fn app_settings_path() -> PathBuf {
    workspace_root().join("data").join("app-settings.json")
}

fn hide_on_windows(command: &mut Command) {
    #[cfg(windows)]
    {
        command.creation_flags(CREATE_NO_WINDOW);
    }
}

fn spawn_production_backend_process(app: &AppHandle) -> Result<Child, String> {
    let backend_bin = bundled_backend_executable(app)
        .or_else(local_release_backend_executable)
        .ok_or_else(|| {
            "production backend executable not found. Expected 'fracture-backend.exe' in bundled resources or apps/backend/dist."
                .to_string()
        })?;

    let mut cmd = Command::new(backend_bin);
    hide_on_windows(&mut cmd);
    cmd.stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("failed to start production backend executable: {e}"))
}

fn spawn_dev_backend_process() -> Result<Child, String> {
    let dev_dir = dev_backend_dir()
        .ok_or_else(|| "dev backend directory not found at apps/backend".to_string())?;
    let venv_python = dev_dir.join(".venv").join("Scripts").join("python.exe");
    let python = if venv_python.exists() {
        venv_python
    } else {
        PathBuf::from("python")
    };

    let mut cmd = Command::new(python);
    cmd.args([
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
        "--app-dir",
        ".",
    ]);
    cmd.current_dir(dev_dir);
    hide_on_windows(&mut cmd);
    cmd.stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("failed to start dev python backend: {e}"))
}

fn spawn_backend_process(app: &AppHandle) -> Result<Child, String> {
    if cfg!(debug_assertions) {
        return spawn_dev_backend_process();
    }

    spawn_production_backend_process(app)
}

fn wait_for_backend_ready(timeout: std::time::Duration) -> Result<(), String> {
    let started = std::time::Instant::now();
    let mut last_error = "backend did not respond".to_string();

    while started.elapsed() < timeout {
        match call_local_api("GET", "/health", "") {
            Ok(_) => return Ok(()),
            Err(error) => {
                last_error = error;
                thread::sleep(std::time::Duration::from_millis(200));
            }
        }
    }

    Err(format!("backend did not become ready: {last_error}"))
}

fn call_local_api(method: &str, path: &str, body: &str) -> Result<String, String> {
    let mut stream = TcpStream::connect((BACKEND_HOST, BACKEND_PORT))
        .map_err(|e| format!("connect failed: {e}"))?;

    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: {BACKEND_HOST}\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{}",
        body.as_bytes().len(),
        body
    );

    stream
        .write_all(request.as_bytes())
        .map_err(|e| format!("request write failed: {e}"))?;

    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|e| format!("response read failed: {e}"))?;

    let status_line = response.lines().next().unwrap_or_default().to_string();
    if !status_line.contains(" 200 ") {
        return Err(format!("api call failed: {status_line}"));
    }

    if let Some(idx) = response.find("\r\n\r\n") {
        return Ok(response[idx + 4..].to_string());
    }

    Ok(String::new())
}

fn emit_ui_log(level: &str, message: &str, source: &str) {
    let payload = serde_json::json!({
        "level": level,
        "message": message,
        "source": source,
    });
    let _ = call_local_api("POST", "/api/logs", &payload.to_string());
}

fn read_app_settings() -> AppSettings {
    let path = app_settings_path();
    let raw = match fs::read_to_string(path) {
        Ok(value) => value,
        Err(_) => return AppSettings::default(),
    };
    serde_json::from_str::<AppSettings>(&raw).unwrap_or_default()
}

fn read_runtime_status() -> RuntimeStatus {
    let payload = match call_local_api("GET", "/api/core/status", "") {
        Ok(value) => value,
        Err(_) => return RuntimeStatus::default(),
    };
    serde_json::from_str::<RuntimeStatus>(&payload).unwrap_or_default()
}

fn write_startup_registry(enabled: bool) -> Result<(), String> {
    #[cfg(not(windows))]
    {
        let _ = enabled;
        return Ok(());
    }

    #[cfg(windows)]
    {
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let run_key = hkcu
            .open_subkey_with_flags(RUN_REG_PATH, KEY_READ | KEY_WRITE)
            .or_else(|_| hkcu.create_subkey(RUN_REG_PATH).map(|pair| pair.0))
            .map_err(|e| format!("failed to open Windows startup registry key: {e}"))?;

        if enabled {
            let exe = std::env::current_exe()
                .map_err(|e| format!("failed to resolve current executable: {e}"))?;
            let command = format!("\"{}\"", exe.display());
            run_key
                .set_value(RUN_VALUE_NAME, &command)
                .map_err(|e| format!("failed to enable startup: {e}"))?;
        } else {
            match run_key.delete_value(RUN_VALUE_NAME) {
                Ok(_) => {}
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(format!("failed to disable startup: {error}")),
            }
        }

        Ok(())
    }
}

fn sync_startup_from_settings() -> Result<(), String> {
    let settings = read_app_settings();
    write_startup_registry(settings.ui.run_on_startup)
}

fn menu_items(
    app: &AppHandle,
) -> Result<
    (
        MenuItem<tauri::Wry>,
        MenuItem<tauri::Wry>,
        MenuItem<tauri::Wry>,
    ),
    tauri::Error,
> {
    let connect_item = MenuItem::with_id(app, "toggle_connection", "Connect", true, None::<&str>)?;
    let lan_item = MenuItem::with_id(app, "toggle_lan", "LAN Sharing: Off", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Exit", true, None::<&str>)?;
    Ok((connect_item, lan_item, quit_item))
}

fn refresh_tray_menu(app: &AppHandle) {
    let Ok((connect_item, lan_item, quit_item)) = menu_items(app) else {
        return;
    };

    let status = read_runtime_status();
    let lan_on = status.proxy_scope.eq_ignore_ascii_case("lan");
    let connected = status.state == "running" || status.state == "starting";

    let _ = connect_item.set_text(if connected { "Disconnect" } else { "Connect" });
    let _ = lan_item.set_text(if lan_on {
        "LAN Sharing: On"
    } else {
        "LAN Sharing: Off"
    });

    let Ok(menu) = Menu::with_items(app, &[&connect_item, &lan_item, &quit_item]) else {
        return;
    };

    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let _ = tray.set_menu(Some(menu));
    }
}

fn tray_toggle_connection(app: &AppHandle) {
    let app_handle = app.clone();
    thread::spawn(move || {
        let status = read_runtime_status();
        let was_connecting = status.state == "running" || status.state == "starting";
        let result = if was_connecting {
            emit_ui_log("debug", "Disconnect requested from tray", "tray");
            call_local_api("POST", "/api/core/stop", "{}")
        } else {
            emit_ui_log("debug", "Connect requested from tray", "tray");
            call_local_api("POST", "/api/core/start", r#"{"profile_id":null}"#)
        };
        match result {
            Ok(_) => {
                if was_connecting {
                    emit_ui_log("info", "Disconnected from tray", "tray");
                } else {
                    emit_ui_log("info", "Connected from tray", "tray");
                }
            }
            Err(error) => {
                emit_ui_log(
                    "error",
                    &format!("Tray connection action failed: {error}"),
                    "tray",
                );
            }
        }
        refresh_tray_menu(&app_handle);
    });
}

fn tray_toggle_lan(app: &AppHandle) {
    let app_handle = app.clone();
    thread::spawn(move || {
        let settings_payload = match call_local_api("GET", "/api/settings/core", "") {
            Ok(value) => value,
            Err(_) => return,
        };

        let mut payload: Value = serde_json::from_str(&settings_payload).unwrap_or(Value::Null);
        let current = payload
            .get("proxyScope")
            .and_then(|value| value.as_str())
            .unwrap_or("local");
        let next = if current.eq_ignore_ascii_case("lan") {
            "local"
        } else {
            "lan"
        };
        if let Some(object) = payload.as_object_mut() {
            object.insert("proxyScope".to_string(), Value::String(next.to_string()));
        } else {
            return;
        }

        let _ = call_local_api("POST", "/api/settings/core", &payload.to_string());
        refresh_tray_menu(&app_handle);
    });
}

#[cfg(windows)]
fn kill_process_tree(pid: u32) {
    let mut cmd = Command::new("taskkill");
    cmd.args(["/PID", &pid.to_string(), "/T", "/F"]);
    hide_on_windows(&mut cmd);
    let _ = cmd.stdout(Stdio::null()).stderr(Stdio::null()).status();
}

#[cfg(windows)]
fn kill_backend_port_owners() {
    let script = format!(
        "$current=$PID; \
         $owners=Get-NetTCPConnection -LocalAddress {BACKEND_HOST} -LocalPort {BACKEND_PORT} -ErrorAction SilentlyContinue | \
           Select-Object -ExpandProperty OwningProcess -Unique; \
         foreach ($owner in $owners) {{ if ($owner -and $owner -ne $current) {{ Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue }} }}"
    );
    let mut cmd = Command::new("powershell");
    cmd.args([
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        &script,
    ]);
    hide_on_windows(&mut cmd);
    let _ = cmd.stdout(Stdio::null()).stderr(Stdio::null()).status();
}

#[cfg(not(windows))]
fn kill_backend_port_owners() {}

fn terminate_backend_child(mut child: Child) {
    let _ = call_local_api("POST", "/api/core/stop", "{}");

    #[cfg(windows)]
    {
        let pid = child.id();
        kill_process_tree(pid);
    }

    #[cfg(not(windows))]
    {
        let _ = child.kill();
    }

    let _ = child.wait();
    kill_backend_port_owners();
}

fn begin_backend_shutdown(app: AppHandle) {
    if BACKEND_SHUTTING_DOWN.swap(true, Ordering::SeqCst) {
        return;
    }

    thread::spawn(move || {
        let _ = stop_backend();
        app.exit(0);
    });
}

#[tauri::command]
fn start_backend(app: AppHandle) -> Result<(), String> {
    {
        let mut guard = BACKEND_CHILD
            .lock()
            .map_err(|_| "backend lock poisoned".to_string())?;

        if let Some(child) = guard.as_mut() {
            match child.try_wait() {
                Ok(Some(_)) => {
                    *guard = None;
                }
                Ok(None) => {
                    if call_local_api("GET", "/health", "").is_ok() {
                        return Ok(());
                    }
                    if let Some(child) = guard.take() {
                        terminate_backend_child(child);
                    }
                }
                Err(error) => {
                    *guard = None;
                    return Err(format!("failed to inspect backend process: {error}"));
                }
            }
        }

        let _ = call_local_api("POST", "/api/core/stop", "{}");
        thread::sleep(std::time::Duration::from_millis(300));
        kill_backend_port_owners();

        let child = spawn_backend_process(&app)?;
        *guard = Some(child);
    }

    if let Err(error) = wait_for_backend_ready(std::time::Duration::from_secs(10)) {
        let _ = stop_backend();
        return Err(error);
    }

    Ok(())
}

#[tauri::command]
fn stop_backend() -> Result<(), String> {
    let _ = call_local_api("POST", "/api/core/stop", "{}");
    let mut guard = BACKEND_CHILD
        .lock()
        .map_err(|_| "backend lock poisoned".to_string())?;
    if let Some(child) = guard.take() {
        terminate_backend_child(child);
    } else {
        kill_backend_port_owners();
    }
    Ok(())
}

#[tauri::command]
fn apply_shell_settings(app: AppHandle) -> Result<(), String> {
    sync_startup_from_settings()?;
    refresh_tray_menu(&app);
    Ok(())
}

#[tauri::command]
fn hide_to_tray(app: AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
    Ok(())
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn should_close_to_tray() -> bool {
    read_app_settings().ui.close_to_tray
}

#[tauri::command]
#[cfg(target_os = "windows")]
fn open_external_url(url: String) -> Result<(), String> {
    Command::new("cmd")
        .args(["/C", "start", "", &url])
        .spawn()
        .map_err(|e| format!("failed to open browser: {e}"))?;
    Ok(())
}

#[tauri::command]
#[cfg(target_os = "macos")]
fn open_external_url(url: String) -> Result<(), String> {
    Command::new("open")
        .arg(&url)
        .spawn()
        .map_err(|e| format!("failed to open browser: {e}"))?;
    Ok(())
}

#[tauri::command]
#[cfg(all(unix, not(target_os = "macos")))]
fn open_external_url(url: String) -> Result<(), String> {
    Command::new("xdg-open")
        .arg(&url)
        .spawn()
        .map_err(|e| format!("failed to open browser: {e}"))?;
    Ok(())
}

#[tauri::command]
fn read_import_file_texts(paths: Vec<String>) -> Result<Vec<String>, String> {
    if paths.is_empty() {
        return Ok(Vec::new());
    }

    let mut payloads = Vec::with_capacity(paths.len());
    for path in paths {
        if !path.to_lowercase().ends_with(".txt") {
            return Err("Only .txt files can be dropped here".to_string());
        }

        let content = fs::read_to_string(&path)
            .map_err(|error| format!("failed to read dropped file '{path}': {error}"))?;
        payloads.push(content);
    }

    Ok(payloads)
}

pub fn run() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let (connect_item, lan_item, quit_item) = menu_items(&app.handle())?;
            let menu = Menu::with_items(app, &[&connect_item, &lan_item, &quit_item])?;

            let handle = app.handle().clone();
            TrayIconBuilder::with_id(TRAY_ID)
                .menu(&menu)
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    "toggle_connection" => tray_toggle_connection(app),
                    "toggle_lan" => tray_toggle_lan(app),
                    "quit" => {
                        begin_backend_shutdown(app.clone());
                    }
                    _ => {}
                })
                .on_tray_icon_event(move |tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main_window(tray.app_handle());
                    }
                })
                .build(app)?;

            let _ = start_backend(app.handle().clone());
            let _ = sync_startup_from_settings();
            refresh_tray_menu(&handle);
            show_main_window(&handle);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            apply_shell_settings,
            hide_to_tray,
            open_external_url,
            read_import_file_texts
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if should_close_to_tray() {
                    api.prevent_close();
                    let _ = window.hide();
                } else {
                    api.prevent_close();
                    let _ = window.hide();
                    begin_backend_shutdown(window.app_handle().clone());
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|handle, event| match event {
        tauri::RunEvent::Ready => {
            refresh_tray_menu(handle);
        }
        _ => {}
    });
}
