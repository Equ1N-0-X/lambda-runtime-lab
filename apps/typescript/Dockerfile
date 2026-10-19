# syntax=docker/dockerfile:1.7

# Node.jsのバージョンと最終OSイメージをビルド引数にし、全言語で最終ベースを固定できるようにする。
ARG NODE_VERSION=24
ARG FINAL_BASE=public.ecr.aws/lambda/provided:al2023

# AWS公式Node.jsイメージから、Node.jsランタイム（/var/lang）とLambdaのランタイム
# インターフェース（/var/runtime）を取得する。管理ランタイムと同じ起動経路を共通ベースの上に再現する。
FROM public.ecr.aws/lambda/nodejs:${NODE_VERSION} AS node-runtime

# lockファイルに固定した依存で、アプリを依存込みの単一ファイルへバンドルする。
FROM node-runtime AS build
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json ./
COPY src ./src
RUN npm run build

# 比較対象の最終コンテナは共通AL2023を使い、ビルド情報と対象アーキテクチャをラベルへ残す。
FROM ${FINAL_BASE} AS runtime
ARG BUILD_DATE=unknown
ARG GIT_REVISION=unknown
ARG TARGETARCH
LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${GIT_REVISION}" \
      org.opencontainers.image.title="lambda-runtime-lab-typescript" \
      org.opencontainers.image.vendor="lambda-runtime-lab" \
      io.lambda-runtime-lab.implementation="typescript" \
      io.lambda-runtime-lab.target-architecture="${TARGETARCH}"

# PATHとLD_LIBRARY_PATHは共通ベースが /var/lang・/var/runtime を含めて設定済みのため、ここでは足さない。
ENV NODE_ENV=production

# Node.jsランタイム、ランタイムインターフェース、バンドル済みアプリを責務別の固定パスへ配置する。
COPY --from=node-runtime /var/lang /var/lang
COPY --from=node-runtime /var/runtime /var/runtime
COPY --from=build /build/dist/index.mjs ${LAMBDA_TASK_ROOT}/index.mjs

WORKDIR ${LAMBDA_TASK_ROOT}
# 共通ベースの /lambda-entrypoint.sh が /var/runtime/bootstrap を起動し、index.handler を呼ぶ。
CMD ["index.handler"]
