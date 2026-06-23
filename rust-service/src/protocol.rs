//! Wire protocol shared with the Home Assistant integration (JSON over WebSocket).

use serde::{Deserialize, Serialize};

fn default_transport() -> String {
    "tcp".into()
}
fn default_fps() -> f32 {
    4.0
}
fn default_width() -> u32 {
    640
}
fn default_detectors() -> Vec<String> {
    vec!["qr".into()]
}

/// Messages sent by the client (Home Assistant) to the service.
#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ClientMsg {
    /// Begin processing a stream. Sent once, right after connecting.
    Start {
        stream_url: String,
        #[serde(default = "default_transport")]
        rtsp_transport: String,
        #[serde(default = "default_fps")]
        fps: f32,
        #[serde(default = "default_width")]
        width: u32,
        /// Requested detectors, e.g. ["qr"]. "people" is reserved for later.
        #[serde(default = "default_detectors")]
        #[allow(dead_code)]
        detectors: Vec<String>,
    },
    /// Stop processing and close.
    Stop,
}

/// Messages sent by the service back to the client.
#[derive(Debug, Serialize, Clone)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerMsg {
    /// Pipeline status / heartbeat.
    Status {
        state: String, // connecting | streaming | reconnecting
        frames: u64,
        last_error: Option<String>,
    },
    /// A detected code.
    Scan {
        payload: String,
        symbol_type: String,
        ts: String, // RFC3339
    },
    /// A non-fatal error (e.g. a malformed client message).
    Error { message: String },
}
