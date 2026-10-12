# syntax=docker/dockerfile:1.7
ARG TARGETPLATFORM=linux/arm64

# ビルダーとランタイムを同じAmazon Linux 2023系にし、動的リンクするglibcを一致させる。
# Debianでは新しいglibcを要求する可能性があり、Alpineではmusl静的リンクによりアロケーター挙動が
# 変わるため、比較結果へビルド環境差を混入させない。
ARG BUILDER_BASE=public.ecr.aws/amazonlinux/amazonlinux:2023
ARG FINAL_BASE=public.ecr.aws/lambda/provided:al2023
ARG RUST_VERSION=1.98.1

# 対象プラットフォームと同じAL2023上へ、固定Rustツールチェーンと最小ビルド依存を導入する。
FROM --platform=$TARGETPLATFORM ${BUILDER_BASE} AS builder
ARG RUST_VERSION
ENV CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH
RUN dnf --setopt=install_weak_deps=False --assumeyes install gcc tar gzip \
    && dnf clean all \
    && curl --proto '=https' --tlsv1.2 --silent --show-error --fail https://sh.rustup.rs \
    | sh -s -- --no-modify-path --profile minimal --default-toolchain "${RUST_VERSION}" -y

WORKDIR /build
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY src ./src

# lockファイルとツールチェーンを尊重してreleaseビルドし、Lambda custom runtime名bootstrapで配置する。
RUN cargo build --locked --release \
    && install -D -m 0755 target/release/rust-lambdalith /out/bootstrap

# 共通の最終ベースへ単一バイナリだけをコピーし、ビルダーやCargoキャッシュを含めない。
FROM --platform=$TARGETPLATFORM ${FINAL_BASE} AS runtime

ARG BUILD_DATE=""
ARG GIT_REVISION=""
ARG IMAGE_SOURCE=""
ARG TARGET_ARCH="arm64"

LABEL org.opencontainers.image.created=$BUILD_DATE \
      org.opencontainers.image.revision=$GIT_REVISION \
      org.opencontainers.image.source=$IMAGE_SOURCE \
      org.opencontainers.image.title="Rust Lambdalith" \
      io.lambda-runtime-lab.implementation="rust" \
      io.lambda-runtime-lab.target-architecture=$TARGET_ARCH

COPY --from=builder /out/bootstrap ${LAMBDA_RUNTIME_DIR}/bootstrap

CMD ["rust-lambdalith"]
