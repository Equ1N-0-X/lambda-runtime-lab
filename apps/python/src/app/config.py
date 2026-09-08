import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    table_name: str
    aws_region: str


def load_settings() -> Settings:
    return Settings(
        table_name=os.getenv("DYNAMODB_TABLE_NAME", "lambda-runtime-lab-items"),
        aws_region=os.getenv("AWS_REGION", "ap-northeast-1"),
    )


SETTINGS = load_settings()
