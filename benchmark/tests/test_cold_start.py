"""関数の設定を更新してコールドスタートを起こし、計測後にTerraformの設定へ戻すことを検証する。"""

from benchmark_runner.cold_start import NONCE_VARIABLE, LambdaColdStarter


class _FakeLambda:
    def __init__(self, variables):
        self.variables = dict(variables)
        self.updates = []

    def get_function_configuration(self, FunctionName):
        return {"Environment": {"Variables": dict(self.variables)}}

    def update_function_configuration(self, FunctionName, Environment):
        self.updates.append(Environment["Variables"])
        self.variables = dict(Environment["Variables"])

    def get_waiter(self, name):
        assert name == "function_updated_v2"
        return self

    def wait(self, FunctionName):
        return None


def test_force_changes_only_the_nonce_and_keeps_existing_variables():
    client = _FakeLambda({"DYNAMODB_TABLE_NAME": "table"})
    starter = LambdaColdStarter("ap-northeast-1", client=client)

    starter.force("fn")
    first = client.variables[NONCE_VARIABLE]
    starter.force("fn")

    # 環境変数は全体を置き換える仕様のため、既存の値を残したまま毎回違う値を入れる。
    assert client.variables["DYNAMODB_TABLE_NAME"] == "table"
    assert client.variables[NONCE_VARIABLE] != first


def test_restore_removes_the_nonce_and_skips_when_already_clean():
    client = _FakeLambda({"DYNAMODB_TABLE_NAME": "table", NONCE_VARIABLE: "x"})
    starter = LambdaColdStarter("ap-northeast-1", client=client)

    starter.restore("fn")
    starter.restore("fn")

    assert client.variables == {"DYNAMODB_TABLE_NAME": "table"}
    # 計測用の値がなくなった後は、設定を更新しない。
    assert len(client.updates) == 1
