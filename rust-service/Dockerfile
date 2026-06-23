# Build stage
FROM rust:1-bookworm AS build
WORKDIR /app
COPY Cargo.toml ./
COPY src ./src
RUN cargo build --release

# Runtime stage
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/target/release/qr-vision-service /usr/local/bin/qr-vision-service

ENV BIND_ADDR=0.0.0.0:8723
EXPOSE 8723
ENTRYPOINT ["qr-vision-service"]

# --- GPU note ---------------------------------------------------------------
# For NVIDIA hardware decode, base the runtime on an image whose ffmpeg has
# nvdec/cuda support (e.g. nvidia/cuda + a cuda-enabled ffmpeg build), run the
# container with `--gpus all`, and set FFMPEG_HWACCEL=cuda. The wire protocol
# and the rest of the service stay identical.
