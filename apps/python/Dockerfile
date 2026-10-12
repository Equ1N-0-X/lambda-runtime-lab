# syntax=docker/dockerfile:1.7

# Pythonバージョンと最終OSイメージをビルド引数にし、両実装で最終ベースを固定できるようにする。
ARG PYTHON_VERSION=3.13
ARG FINAL_BASE=public.ecr.aws/lambda/provided:al2023

# AWS公式Pythonイメージからランタイムを、uv公式イメージから依存解決ツールだけを取得する。
FROM public.ecr.aws/lambda/python:${PYTHON_VERSION} AS python-runtime
FROM ghcr.io/astral-sh/uv:0.8.13 AS uv-bin

# lockファイルに固定された本番依存だけを、最終イメージへコピー可能な仮想環境へ展開する。
FROM python-runtime AS dependencies
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=uv-bin /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 比較対象の最終コンテナは共通AL2023を使い、ビルド情報と対象アーキテクチャをラベルへ残す。
FROM ${FINAL_BASE} AS runtime
ARG BUILD_DATE=unknown
ARG GIT_REVISION=unknown
ARG TARGETARCH
LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${GIT_REVISION}" \
      org.opencontainers.image.title="lambda-runtime-lab-python" \
      org.opencontainers.image.vendor="lambda-runtime-lab" \
      io.lambda-runtime-lab.implementation="python" \
      io.lambda-runtime-lab.target-architecture="${TARGETARCH}"

ENV LAMBDA_TASK_ROOT=/var/task \
    PATH=/var/lang/bin:${PATH} \
    PYTHONPATH=/var/task:/opt/python \
    PYTHONUNBUFFERED=1

# Pythonランタイム、依存パッケージ、アプリケーションを責務別の固定パスへ配置する。
COPY --from=python-runtime /var/lang /var/lang
COPY --from=dependencies /opt/venv/lib/python3.13/site-packages/ /opt/python/
COPY src/app/ ${LAMBDA_TASK_ROOT}/app/

WORKDIR ${LAMBDA_TASK_ROOT}
# AWS Lambda Runtime Interface Clientを起動し、FastAPIを包むMangumハンドラーへイベントを渡す。
ENTRYPOINT ["/var/lang/bin/python3.13", "-m", "awslambdaric"]
CMD ["app.main.handler"]
