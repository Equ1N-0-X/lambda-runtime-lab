# syntax=docker/dockerfile:1.7
ARG TARGETPLATFORM=linux/arm64

# ビルダーとランタイムを同じAmazon Linux 2023系にし、動的リンクするglibcを一致させる。
# Debianでは新しいglibcを要求する可能性があり、Alpineではmusl静的リンクによりアロケーター挙動が
# 変わるため、比較結果へビルド環境差を混入させない。
ARG BUILDER_BASE=public.ecr.aws/amazonlinux/amazonlinux:2023
ARG FINAL_BASE=public.ecr.aws/lambda/provided:al2023
ARG RUST_VERSION=1.98.1
# rustupのインストーラーは版とSHA-256で固定する。取得の都度変わりうるスクリプトは実行しない。
# 版を上げるときは https://static.rust-lang.org/rustup/archive/<版>/<ターゲット>/rustup-init.sha256 の値に更新する。
ARG RUSTUP_VERSION=1.29.3
ARG RUSTUP_SHA256_ARM64=9f608c142e0e38a78957d0661ba1dfaf0b6fb37184cfaa4b9a0aa57af6184282
ARG RUSTUP_SHA256_AMD64=b1c8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55

# 対象プラットフォームと同じAL2023上へ、固定Rustツールチェーンと最小ビルド依存を導入する。
FROM --platform=$TARGETPLATFORM ${BUILDER_BASE} AS builder
ARG RUST_VERSION
ARG RUSTUP_VERSION
ARG RUSTUP_SHA256_ARM64
ARG RUSTUP_SHA256_AMD64
ARG TARGETARCH
ENV CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH
# 対象の方式に合うインストーラーを選び、チェックサムが一致した場合だけ実行する。
RUN dnf --setopt=install_weak_deps=False --assumeyes install gcc tar gzip \
    && dnf clean all \
    && case "${TARGETARCH}" in \
         arm64) target=aarch64-unknown-linux-gnu; sha256="${RUSTUP_SHA256_ARM64}" ;; \
         amd64) target=x86_64-unknown-linux-gnu; sha256="${RUSTUP_SHA256_AMD64}" ;; \
         *) echo "未対応の方式です: ${TARGETARCH}" >&2; exit 1 ;; \
       esac \
    && curl --proto '=https' --tlsv1.2 --silent --show-error --fail --output /tmp/rustup-init \
         "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/${target}/rustup-init" \
    && echo "${sha256}  /tmp/rustup-init" | sha256sum --check --quiet \
    && chmod +x /tmp/rustup-init \
    && /tmp/rustup-init --no-modify-path --profile minimal --default-toolchain "${RUST_VERSION}" -y \
    && rm /tmp/rustup-init

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
# BuildKitがビルド対象の方式（arm64・amd64）を自動で設定する。固定値にすると実体と食い違う。
ARG TARGETARCH

LABEL org.opencontainers.image.created=$BUILD_DATE \
      org.opencontainers.image.revision=$GIT_REVISION \
      org.opencontainers.image.source=$IMAGE_SOURCE \
      org.opencontainers.image.title="Rust Lambdalith" \
      io.lambda-runtime-lab.implementation="rust" \
      io.lambda-runtime-lab.target-architecture=$TARGETARCH

COPY --from=builder /out/bootstrap ${LAMBDA_RUNTIME_DIR}/bootstrap

CMD ["rust-lambdalith"]
