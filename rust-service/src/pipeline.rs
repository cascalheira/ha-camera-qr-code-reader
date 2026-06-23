//! Video pipeline: ffmpeg pulls low-rate MJPEG frames, each is QR-decoded, and
//! detections are streamed back over an mpsc channel. Supervised with backoff.

use std::collections::HashMap;
use std::process::Stdio;
use std::time::{Duration, Instant};

use tokio::io::AsyncReadExt;
use tokio::process::Command;
use tokio::sync::mpsc;

use crate::protocol::ServerMsg;
use crate::qr;

const READ_CHUNK: usize = 65536;
const MAX_BACKOFF: u64 = 30;
const STATUS_EVERY: Duration = Duration::from_secs(5);
const JPEG_SOI: [u8; 2] = [0xFF, 0xD8];
const JPEG_EOI: [u8; 2] = [0xFF, 0xD9];

#[derive(Clone)]
pub struct StreamConfig {
    pub stream_url: String,
    pub rtsp_transport: String,
    pub fps: f32,
    pub width: u32,
    pub ffmpeg: String,
    pub hwaccel: Option<String>,
    pub cooldown: Duration,
}

/// Supervisor loop: (re)connect to the stream with exponential backoff until the
/// client disconnects (the channel closes).
pub async fn run(cfg: StreamConfig, tx: mpsc::Sender<ServerMsg>) {
    let mut frames: u64 = 0;
    let mut backoff = 1u64;

    while !tx.is_closed() {
        let _ = tx
            .send(ServerMsg::Status {
                state: "connecting".into(),
                frames,
                last_error: None,
            })
            .await;

        match run_once(&cfg, &tx, &mut frames).await {
            Ok(()) => backoff = 1,
            Err(err) => {
                tracing::warn!("stream error: {err}");
                let _ = tx
                    .send(ServerMsg::Status {
                        state: "reconnecting".into(),
                        frames,
                        last_error: Some(err.to_string()),
                    })
                    .await;
            }
        }

        if tx.is_closed() {
            break;
        }
        tokio::time::sleep(Duration::from_secs(backoff)).await;
        backoff = (backoff * 2).min(MAX_BACKOFF);
    }
    tracing::info!("pipeline stopped");
}

async fn run_once(
    cfg: &StreamConfig,
    tx: &mpsc::Sender<ServerMsg>,
    frames: &mut u64,
) -> anyhow::Result<()> {
    let mut cmd = Command::new(&cfg.ffmpeg);
    cmd.arg("-nostdin").arg("-loglevel").arg("error");
    if let Some(hw) = &cfg.hwaccel {
        cmd.arg("-hwaccel").arg(hw);
    }
    cmd.arg("-rtsp_transport")
        .arg(&cfg.rtsp_transport)
        .arg("-i")
        .arg(&cfg.stream_url)
        .arg("-an")
        .arg("-vf")
        .arg(format!("fps={},scale={}:-2", cfg.fps, cfg.width))
        .arg("-f")
        .arg("image2pipe")
        .arg("-vcodec")
        .arg("mjpeg")
        .arg("-q:v")
        .arg("5")
        .arg("pipe:1")
        .stdout(Stdio::piped())
        .stderr(Stdio::null());

    let mut child = cmd.spawn()?;
    let mut stdout = child
        .stdout
        .take()
        .ok_or_else(|| anyhow::anyhow!("ffmpeg produced no stdout"))?;

    let mut buffer: Vec<u8> = Vec::with_capacity(1 << 20);
    let mut chunk = vec![0u8; READ_CHUNK];
    let mut last_seen: HashMap<String, Instant> = HashMap::new();
    let mut last_status = Instant::now();
    let mut streaming = false;

    let result = loop {
        let read = match stdout.read(&mut chunk).await {
            Ok(0) => break Ok(()), // ffmpeg exited
            Ok(n) => n,
            Err(err) => break Err(anyhow::Error::from(err)),
        };

        if !streaming {
            streaming = true;
            let _ = tx
                .send(ServerMsg::Status {
                    state: "streaming".into(),
                    frames: *frames,
                    last_error: None,
                })
                .await;
        }

        buffer.extend_from_slice(&chunk[..read]);
        for frame in extract_frames(&mut buffer) {
            *frames += 1;
            let codes = tokio::task::spawn_blocking(move || qr::decode_qr(&frame))
                .await
                .unwrap_or_default();
            let now = Instant::now();
            for payload in codes {
                if let Some(prev) = last_seen.get(&payload) {
                    if now.duration_since(*prev) < cfg.cooldown {
                        continue;
                    }
                }
                last_seen.insert(payload.clone(), now);
                let _ = tx
                    .send(ServerMsg::Scan {
                        payload,
                        symbol_type: "QRCODE".into(),
                        ts: chrono::Utc::now().to_rfc3339(),
                    })
                    .await;
            }
            if tx.is_closed() {
                break;
            }
        }

        if tx.is_closed() {
            break Ok(());
        }
        if last_status.elapsed() >= STATUS_EVERY {
            last_status = Instant::now();
            let _ = tx
                .send(ServerMsg::Status {
                    state: "streaming".into(),
                    frames: *frames,
                    last_error: None,
                })
                .await;
        }
    };

    let _ = child.kill().await;
    result
}

/// Pull complete JPEG frames out of the buffer, leaving any partial tail.
fn extract_frames(buffer: &mut Vec<u8>) -> Vec<Vec<u8>> {
    let mut frames = Vec::new();
    loop {
        let Some(start) = find(buffer, &JPEG_SOI, 0) else {
            buffer.clear();
            break;
        };
        let Some(end) = find(buffer, &JPEG_EOI, start + 2) else {
            if start > 0 {
                buffer.drain(0..start);
            }
            break;
        };
        frames.push(buffer[start..end + 2].to_vec());
        buffer.drain(0..end + 2);
    }
    frames
}

fn find(haystack: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    if from >= haystack.len() {
        return None;
    }
    haystack[from..]
        .windows(needle.len())
        .position(|w| w == needle)
        .map(|p| p + from)
}
