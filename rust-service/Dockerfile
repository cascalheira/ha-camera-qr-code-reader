# Build stage
FROM rust:1-bookworm AS build
WORKDIR /app
COPY Cargo.toml ./
COPY src ./src
RUN cargo build --release

# Runtime stage
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg ca-certificates \
        mesa-va-drivers libva2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/target/release/qr-vision-service /usr/local/bin/qr-vision-service

ENV BIND_ADDR=0.0.0.0:8723
EXPOSE 8723
ENTRYPOINT ["qr-vision-service"]

# --- GPU note ---------------------------------------------------------------
# AMD / Intel (Linux, VAAPI): this Debian image's ffmpeg already supports VAAPI.
#   Run with `--device /dev/dri`, set FFMPEG_HWACCEL=vaapi and
#   FFMPEG_HWACCEL_DEVICE=/dev/dri/renderD128.
# NVIDIA (CUDA): base the runtime on a cuda-enabled ffmpeg image, run with
#   `--gpus all`, set FFMPEG_HWACCEL=cuda.
# The wire protocol and the rest of the service stay identical.
