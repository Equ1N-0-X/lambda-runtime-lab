"""Dockerfileが参照する外部のベースイメージを求め、ビルドの入力として記録できるようにする。

ベースイメージはタグで指定しているため、同じGitのコミットでも、タグが新しい版へ移ると
別の中身のイメージができる。ビルドのたびに実際に使ったdigestを記録し、ビルド元が
変わったことを後から識別できるようにする（digestの解決は呼び出し側のスクリプトが行う）。
"""

from __future__ import annotations

import re

ARG_PATTERN = re.compile(r"^ARG\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:=(?P<value>\S*))?", re.I)
FROM_PATTERN = re.compile(
    r"^FROM\s+(?:--platform=\S+\s+)?(?P<image>\S+)(?:\s+AS\s+(?P<stage>\S+))?", re.I
)
VARIABLE_PATTERN = re.compile(r"\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|\$(?P<bare>[A-Za-z_]\w*)")


def external_base_images(dockerfile: str, build_args: dict[str, str]) -> list[str]:
    """Dockerfileの FROM のうち、前の段階ではない外部のイメージ参照を出現順に返す。

    ARG の既定値と、ビルド時に渡す引数（build_args が優先）で変数を展開する。
    展開できない変数が残る参照は、どのイメージか決められないため含めない。
    """
    values: dict[str, str] = {}
    stages: set[str] = set()
    images: list[str] = []
    for raw_line in dockerfile.splitlines():
        line = raw_line.strip()
        argument = ARG_PATTERN.match(line)
        # ARGは既定値を記録し、ビルド引数で渡された値があればそちらを使う。
        if argument:
            name = argument["name"]
            values[name] = build_args.get(name, argument["value"] or values.get(name, ""))
            continue
        base = FROM_PATTERN.match(line)
        if not base:
            continue
        image = VARIABLE_PATTERN.sub(
            lambda match: values.get(match["braced"] or match["bare"], match[0]), base["image"]
        )
        # 前の段階を土台にする FROM や、展開できなかった参照は外部のイメージではない。
        if image not in stages and "$" not in image and image != "scratch" and image not in images:
            images.append(image)
        if base["stage"]:
            stages.add(base["stage"])
    return images
