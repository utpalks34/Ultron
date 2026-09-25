use rand::Rng;
use tauri::Emitter;
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

const HEX: &[u8] = b"0123456789abcdef";

fn session_token() -> String {
    if cfg!(debug_assertions) {
        std::env::var("ULTRON_SESSION_TOKEN").unwrap_or_else(|_| "dev".to_string())
    } else {
        let mut rng = rand::thread_rng();
        (0..32)
            .map(|_| HEX[rng.gen_range(0..HEX.len())] as char)
            .collect()
    }
}

pub fn run() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        let _ = app.emit("ultron://ptt", ());
                    }
                })
                .build(),
        )
        .setup(|app| {
            let token = session_token();
            // TODO Phase 8: spawn the backend sidecar and pass it this release token.

            // Ctrl+Shift+Space toggles push-to-talk from anywhere, even when the window is
            // unfocused. Change the Code/Modifiers here if it conflicts with something on
            // this machine. Registered BEFORE the window is built so the result can be injected
            // into the page below.
            let shortcut = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space);
            let hotkey_error = match app.global_shortcut().register(shortcut) {
                Ok(()) => "null".to_string(),
                Err(e) => {
                    eprintln!("global hotkey registration failed: {e} - Ctrl+Shift+Space may be in use by another app");
                    serde_json::to_string(&format!(
                        "{e} - Ctrl+Shift+Space may be in use by another app"
                    ))?
                }
            };

            // eprintln! is invisible in a release build (windows_subsystem = "windows"), so this
            // injected variable is the only way a registration failure reaches the user in a
            // packaged build.
            tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::default())
                .title("Ultron")
                .inner_size(1280.0, 800.0)
                .transparent(true)
                .decorations(false)
                .always_on_top(true)
                .resizable(true)
                .initialization_script(&format!(
                    "window.__TAURI_ULTRON_TOKEN__ = {};",
                    serde_json::to_string(&token)?
                ))
                .initialization_script(&format!(
                    "window.__ULTRON_HOTKEY_ERROR__ = {};",
                    hotkey_error
                ))
                .build()?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Ultron");
}
