"""Dockerfileから、ビルドの入力として記録する外部のベースイメージを求められることを検証する。"""

from pathlib import Path

from benchmark_runner.build_inputs import external_base_images

ROOT = Path(__file__).resolve().parents[2]


def test_stage_aliases_are_not_external_images():
    dockerfile = """
ARG PYTHON_VERSION=3.13
ARG FINAL_BASE=public.ecr.aws/lambda/provided:al2023
FROM public.ecr.aws/lambda/python:${PYTHON_VERSION} AS python-runtime
FROM python-runtime AS dependencies
FROM --platform=$TARGETPLATFORM ${FINAL_BASE} AS runtime
"""

    images = external_base_images(dockerfile, {"FINAL_BASE": "base@sha256:abc"})

    # 前の段階を土台にする FROM は除き、ビルド引数はARGの既定値より優先する。
    assert images == ["public.ecr.aws/lambda/python:3.13", "base@sha256:abc"]


def test_unresolved_variables_are_not_guessed():
    assert external_base_images("FROM ${UNKNOWN}:1\nFROM scratch\n", {}) == []


def test_every_dockerfile_uses_the_common_final_base():
    for name in ("python", "rust", "typescript", "java"):
        dockerfile = (ROOT / "apps" / name / "Dockerfile").read_text(encoding="utf-8")

        images = external_base_images(dockerfile, {"FINAL_BASE": "final@sha256:abc"})

        # 全言語の最終イメージは、build-images.sh が解決した共通のベースから作る。
        assert images[-1] == "final@sha256:abc"
        assert len(images) >= 2
