//! Video pipeline: ffmpeg pulls low-rate MJPEG frames, each is QR-decoded, and
//! detections are streamed back over an mpsc channel. Supervised with backoff.

use std::collections::HashMap;
use std::process::Stdio;
use std::time::{Duration, Instant};

use tokio::io::AsyncReadExt;
use tokio::process::Command;
use tokio::sync::{mpsc, watch};

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
    pub hwaccel_device: Option<String>,
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
    // Low-latency input: don't buffer, don't hold a decoder reorder queue.
    cmd.arg("-fflags").arg("nobuffer").arg("-flags").arg("low_delay");
    if let Some(hw) = &cfg.hwaccel {
        cmd.arg("-hwaccel").arg(hw);
        if let Some(dev) = &cfg.hwaccel_device {
            cmd.arg("-hwaccel_device").arg(dev);
        }
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

    let mut child = cmd.spawn().map_err(|err| {
        anyhow::anyhow!(
            "failed to start ffmpeg '{}': {err} \
             (is ffmpeg installed and on PATH, or FFMPEG_PATH set?)",
            cfg.ffmpeg
        )
    })?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| anyhow::anyhow!("ffmpeg produced no stdout"))?;

    // A reader task keeps only the most recent frame; the decoder always grabs
    // the freshest one and drops stale frames, so latency never accumulates.
    let (frame_tx, mut frame_rx) = watch::channel::<Option<Vec<u8>>>(None);
    let reader = tokio::spawn(async move {
        let mut stdout = stdout;
        let mut buffer: Vec<u8> = Vec::with_capacity(1 << 20);
        let mut chunk = vec![0u8; READ_CHUNK];
        loop {
            match stdout.read(&mut chunk).await {
                Ok(0) | Err(_) => break, // ffmpeg exited
                Ok(n) => {
                    buffer.extend_from_slice(&chunk[..n]);
                    for frame in extract_frames(&mut buffer) {
                        // Overwrite: the decoder only ever sees the latest frame.
                        let _ = frame_tx.send(Some(frame));
                    }
                }
            }
        }
        // Dropping frame_tx closes the channel, ending the decoder loop.
    });

    let mut last_seen: HashMap<String, Instant> = HashMap::new();
    let mut last_status = Instant::now();
    let mut streaming = false;

    let result = loop {
        if frame_rx.changed().await.is_err() {
            break Ok(()); // reader ended (ffmpeg exited)
        }
        let Some(frame) = frame_rx.borrow_and_update().clone() else {
            continue;
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

        *frames += 1;
        let codes = tokio::task::spawn_blocking(move || qr::decode_qr(&frame))
            .await
            .unwrap_or_default();
        emit_codes(codes, &mut last_seen, cfg.cooldown, tx).await;

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

    reader.abort();
    let _ = child.kill().await;
    result
}

/// Send detected payloads, debounced per-value by the cooldown.
async fn emit_codes(
    codes: Vec<String>,
    last_seen: &mut HashMap<String, Instant>,
    cooldown: Duration,
    tx: &mpsc::Sender<ServerMsg>,
) {
    let now = Instant::now();
    for payload in codes {
        if let Some(prev) = last_seen.get(&payload) {
            if now.duration_since(*prev) < cooldown {
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
