"""画面の計測設定が、手作業の環境変数なしにTerraformとLambdaの実設定から求まることを検証する。"""

from pathlib import Path

import pytest
from botocore.exceptions import NoCredentialsError

from benchmark_runner.dashboard import settings as settings_module
from benchmark_runner.dashboard.settings import (
    FunctionConditions,
    SettingsResolver,
    SettingsUnavailable,
)
from benchmark_runner.deployment import DeployedImage

REGISTRY = Path(__file__).parent / "fixtures/implementations.yaml"

OUTPUTS = {
    "api_endpoints": {"python": "https://python.example", "rust": "https://rust.example"},
    "lambda_names": {"python": "lab-python", "rust": "lab-rust"},
}
ARM_512 = FunctionConditions(architecture="arm64", memory_mb=512)


def _resolver(*, environ=None, outputs=OUTPUTS, conditions=None, calls=None, check_revision=None):
    conditions = conditions or {"lab-python": ARM_512, "lab-rust": ARM_512}

    def read_outputs(_terraform_dir):
        # 例外を渡された場合は、Terraformの出力を読めない状況を再現する。
        if isinstance(outputs, Exception):
            raise outputs
        return outputs

    def read_conditions(function_name, region):
        if calls is not None:
            calls.append((function_name, region))
        value = conditions[function_name]
        if isinstance(value, Exception):
            raise value
        return value

    return SettingsResolver(
        terraform_dir=Path("unused"),
        registry_path=REGISTRY,
        environ=environ or {},
        read_outputs=read_outputs,
        read_conditions=read_conditions,
        check_revision=check_revision,
        read_image=lambda function_name, region: DeployedImage(
            repository=function_name, digest=f"sha256:{function_name}", tags=("t-gabc1234",)
        ),
    )


def test_resolves_everything_without_environment_variables():
    calls = []

    settings = _resolver(calls=calls).resolve()

    assert settings.missing() == []
    assert settings.endpoints["python"] == "https://python.example"
    assert settings.log_groups["rust"] == "/aws/lambda/lab-rust"
    assert (settings.architecture, settings.memory_mb) == ("arm64", 512)
    assert calls == [("lab-python", "ap-northeast-1"), ("lab-rust", "ap-northeast-1")]
    assert "example" not in str(settings.public())
    assert [item["key"] for item in settings.public()["implementations"]] == ["python", "rust"]


def test_environment_region_takes_precedence_and_matching_endpoint_is_accepted():
    calls = []
    environ = {"PYTHON_API_URL": "https://python.example", "AWS_REGION": "us-east-1"}

    settings = _resolver(environ=environ, calls=calls).resolve()

    assert settings.missing() == []
    assert settings.endpoints["python"] == "https://python.example"
    assert calls[0][1] == "us-east-1"


@pytest.mark.parametrize("name", ["PYTHON_API_URL", "PYTHON_API_ENDPOINT"])
def test_endpoint_override_not_managed_by_terraform_blocks_launch(name):
    settings = _resolver(environ={name: "https://other-env.example"}).resolve()

    # 呼び出すAPIと、設定・ログを読むLambdaが別環境になるため計測させない。
    assert settings.public()["ready"] is False
    assert f"unset {name}" in settings.missing()[0]


def test_endpoint_overrides_of_several_languages_are_reported_as_one_problem():
    environ = {
        "PYTHON_API_URL": "https://other-env.example",
        "RUST_API_URL": "https://other-env.example",
    }

    missing = _resolver(environ=environ).resolve().missing()

    # 原因と対処が同じため、言語の数だけ並べずに一件で示し、一度の unset で解消できるようにする。
    assert len(missing) == 1
    assert "unset PYTHON_API_URL RUST_API_URL" in missing[0]


def test_problem_is_published_in_parts_so_the_screen_can_show_cause_and_remedy_separately():
    settings = _resolver(environ={"PYTHON_API_URL": "https://other-env.example"}).resolve()

    (problem,) = settings.public()["problems"]

    # 何が悪いか、対象、実行するコマンド、実行後にどうなるかを、画面が別々に示せる形で返す。
    assert problem["items"] == ("PYTHON_API_URL",)
    assert problem["command"] == "unset PYTHON_API_URL"
    assert "起動し直して" in problem["action"]
    assert "デプロイ済みの API を計測できる" in problem["effect"]
    # 接続先のURLそのものは画面に返さない。
    assert "other-env.example" not in str(problem)


def test_api_endpoint_takes_precedence_over_api_url_like_the_cli():
    environ = {
        "PYTHON_API_ENDPOINT": "https://python.example",
        "PYTHON_API_URL": "https://stale.example",
    }

    assert _resolver(environ=environ).resolve().missing() == []


def test_checked_deployment_is_kept_as_run_evidence():
    settings = _resolver(check_revision=lambda **kwargs: None).resolve()

    assert settings.deployments["rust"] == {
        "function_name": "lab-rust",
        "architecture": "arm64",
        "memory_mb": 512,
        "image_repository": "lab-rust",
        "image_digest": "sha256:lab-rust",
        "image_tags": ["t-gabc1234"],
    }


def test_terraform_failure_is_reported_as_a_single_actionable_problem():
    settings = _resolver(outputs=SettingsUnavailable("Terraform の出力を読めません。")).resolve()

    assert settings.public()["ready"] is False
    assert settings.missing() == ["Terraform の出力を読めません。"]


def test_auth_failure_is_reported_once_for_all_functions():
    failure = SettingsUnavailable("AWS の認証情報が見つからないか、期限が切れています。")
    conditions = {"lab-python": failure, "lab-rust": failure}

    settings = _resolver(conditions=conditions).resolve()

    assert settings.missing() == [str(failure)]


def test_mismatched_lambda_settings_block_launch():
    conditions = {"lab-python": ARM_512, "lab-rust": FunctionConditions("arm64", 1024)}

    settings = _resolver(conditions=conditions).resolve()

    assert settings.architecture is None
    assert "設定が異なります" in settings.missing()[0]
    assert "1024 MB" in settings.missing()[0]


@pytest.mark.parametrize(
    "environ",
    [{"ARCHITECTURE": "x86_64"}, {"LAMBDA_MEMORY_MB": "256"}],
)
def test_environment_conditions_that_contradict_lambda_block_launch(environ):
    settings = _resolver(environ=environ).resolve()

    assert settings.memory_mb is None
    assert "Lambda の実設定（arm64・512 MB）と異なります" in settings.missing()[0]


def test_matching_environment_conditions_are_accepted():
    environ = {"ARCHITECTURE": "arm64", "LAMBDA_MEMORY_MB": "512"}

    assert _resolver(environ=environ).resolve().missing() == []


def test_implementation_missing_from_terraform_outputs_blocks_launch():
    outputs = {
        "api_endpoints": {"python": "https://python.example"},
        "lambda_names": {"python": "lab-python"},
    }

    settings = _resolver(outputs=outputs).resolve()

    assert settings.public()["ready"] is False
    assert "Rust の Lambda がデプロイされていません" in settings.missing()[0]


def test_stale_deployment_reported_by_revision_check_blocks_launch():
    checked = []

    def check_revision(**kwargs):
        checked.append(kwargs["label"])
        return (
            "Rust のアプリはデプロイ後に変更されています。" if kwargs["label"] == "Rust" else None
        )

    settings = _resolver(check_revision=check_revision).resolve()

    assert checked == ["Python", "Rust"]
    assert settings.missing() == ["Rust のアプリはデプロイ後に変更されています。"]


def test_revision_check_is_skipped_when_settings_already_have_problems():
    def check_revision(**kwargs):
        raise AssertionError("設定に問題がある間は版を確認しない")

    conditions = {"lab-python": ARM_512, "lab-rust": FunctionConditions("arm64", 1024)}

    settings = _resolver(conditions=conditions, check_revision=check_revision).resolve()

    assert "設定が異なります" in settings.missing()[0]


def test_read_lambda_conditions_explains_missing_credentials(monkeypatch):
    class Client:
        def get_function_configuration(self, FunctionName):  # noqa: N803 - boto3の引数名
            raise NoCredentialsError()

    monkeypatch.setattr(settings_module.boto3, "client", lambda *args, **kwargs: Client())

    with pytest.raises(SettingsUnavailable, match="ログイン"):
        settings_module.read_lambda_conditions("lab-python", "ap-northeast-1")


def test_read_terraform_outputs_explains_missing_state(tmp_path):
    # CIなどterraform未導入の環境では、インストールを促す説明になる。
    with pytest.raises(SettingsUnavailable, match="make deploy|インストール"):
        settings_module.read_terraform_outputs(tmp_path / "no-such-dir")
