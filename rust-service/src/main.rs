//! QR Vision Service — remote video-processing worker for the Home Assistant
//! "QR Code RTSP Reader" integration.
//!
//! Home Assistant opens an authenticated WebSocket, sends a `start` message with
//! the stream details, and receives `scan`/`status` events. Heavy work (RTSP
//! decode + QR detection) runs here instead of on the Raspberry Pi.

mod pipeline;
mod protocol;
mod qr;

use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::State;
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use futures_util::{SinkExt, StreamExt};
use tokio::process::Command;
use tokio::sync::mpsc;

use pipeline::StreamConfig;
use protocol::{ClientMsg, ServerMsg};

const DEFAULT_COOLDOWN: Duration = Duration::from_secs(3);

#[derive(Clone)]
struct AppState {
    secret: Arc<String>,
    ffmpeg: String,
    hwaccel: Option<String>,
    hwaccel_device: Option<String>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "qr_vision_service=info,info".into()),
        )
        .init();

    let secret = std::env::var("QR_SERVICE_SECRET")
        .map_err(|_| anyhow::anyhow!("QR_SERVICE_SECRET must be set"))?;
    if secret.len() < 16 {
        anyhow::bail!("QR_SERVICE_SECRET must be at least 16 characters");
    }
    let bind = std::env::var("BIND_ADDR").unwrap_or_else(|_| "0.0.0.0:8723".into());
    let ffmpeg = std::env::var("FFMPEG_PATH").unwrap_or_else(|_| "ffmpeg".into());
    let hwaccel = std::env::var("FFMPEG_HWACCEL").ok().filter(|s| !s.is_empty());
    let hwaccel_device = std::env::var("FFMPEG_HWACCEL_DEVICE")
        .ok()
        .filter(|s| !s.is_empty());

    let state = AppState {
        secret: Arc::new(secret),
        ffmpeg,
        hwaccel,
        hwaccel_device,
    };

    // Probe ffmpeg up front so a missing binary is obvious at startup.
    match Command::new(&state.ffmpeg)
        .arg("-version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await
    {
        Ok(s) if s.success() => tracing::info!("ffmpeg OK: {}", state.ffmpeg),
        Ok(_) => tracing::warn!("ffmpeg '{}' returned non-zero for -version", state.ffmpeg),
        Err(err) => tracing::error!(
            "ffmpeg not found at '{}': {err}. Install ffmpeg or set FFMPEG_PATH.",
            state.ffmpeg
        ),
    }

    let app = Router::new()
        .route("/health", get(|| async { "ok" }))
        .route("/ws", get(ws_handler))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind(&bind).await?;
    tracing::info!("qr-vision-service listening on {bind}");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    Ok(())
}

async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
    tracing::info!("shutting down");
}

/// Authenticate the upgrade request, then hand off to the socket handler.
async fn ws_handler(
    ws: WebSocketUpgrade,
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Response {
    let expected = format!("Bearer {}", state.secret);
    let authorized = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .map(|v| v == expected)
        .unwrap_or(false);
    if !authorized {
        return (StatusCode::UNAUTHORIZED, "unauthorized").into_response();
    }
    ws.on_upgrade(move |socket| handle_socket(socket, state))
}

async fn handle_socket(socket: WebSocket, state: AppState) {
    let (mut sender, mut receiver) = socket.split();

    // Wait for the initial Start message.
    let cfg = loop {
        match receiver.next().await {
            Some(Ok(Message::Text(text))) => match serde_json::from_str::<ClientMsg>(&text) {
                Ok(ClientMsg::Start {
                    stream_url,
                    rtsp_transport,
                    fps,
                    width,
                    detectors: _,
                }) => {
                    break StreamConfig {
                        stream_url,
                        rtsp_transport,
                        fps,
                        width,
                        ffmpeg: state.ffmpeg.clone(),
                        hwaccel: state.hwaccel.clone(),
                        hwaccel_device: state.hwaccel_device.clone(),
                        cooldown: DEFAULT_COOLDOWN,
                    };
                }
                Ok(ClientMsg::Stop) => return,
                Err(err) => {
                    let _ = send(&mut sender, ServerMsg::Error {
                        message: format!("bad message: {err}"),
                    })
                    .await;
                }
            },
            Some(Ok(Message::Close(_))) | None => return,
            Some(Ok(_)) => {} // ignore ping/pong/binary
            Some(Err(_)) => return,
        }
    };

    let (tx, mut rx) = mpsc::channel::<ServerMsg>(64);
    let pipeline = tokio::spawn(pipeline::run(cfg, tx));

    loop {
        tokio::select! {
            outgoing = rx.recv() => match outgoing {
                Some(msg) => {
                    if send(&mut sender, msg).await.is_err() {
                        break;
                    }
                }
                None => break, // pipeline ended
            },
            incoming = receiver.next() => match incoming {
                Some(Ok(Message::Text(text))) => {
                    if matches!(serde_json::from_str::<ClientMsg>(&text), Ok(ClientMsg::Stop)) {
                        break;
                    }
                }
                Some(Ok(Message::Close(_))) | None => break,
                Some(Ok(_)) => {}
                Some(Err(_)) => break,
            },
        }
    }

    pipeline.abort();
}

async fn send(
    sender: &mut futures_util::stream::SplitSink<WebSocket, Message>,
    msg: ServerMsg,
) -> Result<(), axum::Error> {
    let json = serde_json::to_string(&msg).unwrap_or_else(|_| "{}".into());
    sender.send(Message::Text(json)).await
}
