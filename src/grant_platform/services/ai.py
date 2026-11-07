"""The single gateway to the OpenAI API, and local configuration. Credentials are read from the environment or
from a git-ignored .env file and are never printed, logged, stored or sent anywhere but the API."""
import json
import os
from pathlib import Path

from pydantic import ValidationError as SchemaError

from grant_platform.services.matching import DEFAULT_MODEL, GatewayError

ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
_ALLOWED = ("OPENAI_API_KEY", "OPENAI_MODEL")


def load_env(path=ENV_PATH, names=_ALLOWED):
    """Copy OPENAI_API_KEY and OPENAI_MODEL (or the named settings) from a .env file into the environment unless already set."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        name, separator, value = line.strip().partition("=")
        name, value = name.strip(), value.strip().strip("'\"")
        if separator and name in names and value and not os.environ.get(name):
            os.environ[name] = value


def model_name():
    return os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL


def configured():
    return bool(os.environ.get("OPENAI_API_KEY"))


class OpenAIGateway:
    """One request per call, structured output validated by pydantic. Errors are reduced to a short safe message."""

    def __init__(self, model=None, timeout=120.0):
        self.model = model or model_name()
        self.timeout = timeout

    def __call__(self, instructions, payload, schema):
        from openai import OpenAI, OpenAIError  # imported here so tests and screens work without a key

        try:
            with OpenAI(max_retries=0, timeout=self.timeout) as client:
                response = client.responses.parse(
                    model=self.model, instructions=instructions,
                    input=json.dumps(payload, ensure_ascii=False), text_format=schema,
                )
        except OpenAIError as error:
            status = getattr(error, "status_code", None)
            raise GatewayError(f"{type(error).__name__}" + (f", HTTP {status}" if status else "")) from None
        except SchemaError:
            raise GatewayError("answer not in the expected form") from None
        if response.status != "completed" or response.output_parsed is None:
            raise GatewayError(f"incomplete response, status {response.status}")
        return response.output_parsed


def default_gateway():
    """The real gateway when a key is configured on this computer, otherwise None."""
    load_env()
    return OpenAIGateway() if configured() else None
