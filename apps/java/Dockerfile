# syntax=docker/dockerfile:1.7

# Javaのバージョンと最終OSイメージをビルド引数にし、全言語で最終ベースを固定できるようにする。
ARG JAVA_VERSION=25
ARG FINAL_BASE=public.ecr.aws/lambda/provided:al2023

# AWS公式Javaイメージから、JDK（/var/lang）とLambdaのランタイムインターフェース（/var/runtime）を
# 取得する。管理ランタイムと同じ起動経路（JVMの起動オプションを含む）を共通ベースの上に再現する。
FROM public.ecr.aws/lambda/java:${JAVA_VERSION} AS java-runtime

# Mavenで依存を解決してコンパイルし、クラスと依存JARを別々に書き出す。テストはCIで実行する。
FROM maven:3.9-eclipse-temurin-${JAVA_VERSION} AS build
WORKDIR /build
COPY pom.xml ./
RUN mvn -B -q dependency:go-offline
COPY src ./src
RUN mvn -B -q -DskipTests package

# 比較対象の最終コンテナは共通AL2023を使い、ビルド情報と対象アーキテクチャをラベルへ残す。
FROM ${FINAL_BASE} AS runtime
ARG BUILD_DATE=unknown
ARG GIT_REVISION=unknown
ARG TARGETARCH
LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${GIT_REVISION}" \
      org.opencontainers.image.title="lambda-runtime-lab-java" \
      org.opencontainers.image.vendor="lambda-runtime-lab" \
      io.lambda-runtime-lab.implementation="java" \
      io.lambda-runtime-lab.target-architecture="${TARGETARCH}"

# JDK、ランタイムインターフェース、アプリのクラスと依存JARを、Lambdaが読む固定パスへ配置する。
# Lambdaのランタイムは /var/task と /var/task/lib/*.jar をクラスパスに載せる。
COPY --from=java-runtime /var/lang /var/lang
COPY --from=java-runtime /var/runtime /var/runtime
COPY --from=build /build/target/classes ${LAMBDA_TASK_ROOT}
COPY --from=build /build/target/dependency ${LAMBDA_TASK_ROOT}/lib

WORKDIR ${LAMBDA_TASK_ROOT}
# 共通ベースの /lambda-entrypoint.sh が /var/runtime/bootstrap を起動し、このハンドラーを呼ぶ。
CMD ["lab.lambdalith.StreamLambdaHandler::handleRequest"]
