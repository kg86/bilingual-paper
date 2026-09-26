from __future__ import annotations

import json
import os
import sys
import time

from google import genai
from google.genai import errors

DEFAULT_MODEL = "gemini-flash-lite-latest"


def client() -> genai.Client:
    """Build a Vertex AI client authenticated with local Application Default Credentials.

    No API key is used. Run `gcloud auth application-default login` once, and set
    GOOGLE_CLOUD_PROJECT (and optionally GOOGLE_CLOUD_LOCATION) to your project.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT is not set. Authenticate locally with "
            "`gcloud auth application-default login` and export GOOGLE_CLOUD_PROJECT "
            "to your GCP project id (no API key is used)."
        )
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    return genai.Client(vertexai=True, project=project, location=location)


def _is_transient(error: Exception) -> bool:
    if isinstance(error, errors.ServerError):
        return True
    return isinstance(error, errors.ClientError) and error.code in (408, 429)


def generate_json(
    api: genai.Client,
    *,
    model: str,
    instructions: str,
    payload: str,
    schema: dict,
    attempts: int = 4,
    backoff: float = 2.0,
) -> dict:
    """Call the model and parse its JSON reply, retrying transient API errors and empty/invalid output.

    An empty `response.text` (e.g. a safety block or truncated candidate) and unparseable JSON are
    retried like a 5xx/429, since a fresh sample at the same temperature usually succeeds.
    """
    for attempt in range(1, attempts + 1):
        try:
            response = api.models.generate_content(
                model=model,
                contents=payload,
                config={
                    "system_instruction": instructions,
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                    "temperature": 0,
                },
            )
            if not response.text:
                raise ValueError("model returned an empty response")
            return json.loads(response.text)
        except (
            errors.APIError,
            ValueError,
        ) as error:  # json.JSONDecodeError is a ValueError
            if attempt == attempts or (
                isinstance(error, errors.APIError) and not _is_transient(error)
            ):
                raise
            print(
                f"warning: Gemini call failed ({error}); retrying ({attempt}/{attempts - 1})",
                file=sys.stderr,
            )
            time.sleep(backoff * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


def merge_invented_ids(
    expected_ids: set[str], entries: list[dict], text_key: str
) -> list[dict]:
    """Fold entries for IDs the model invented back into the preceding expected entry.

    Small models occasionally split one input segment's response across two consecutive
    entries under a fabricated ID instead of returning a single combined entry, even when
    instructed not to. Since this only adds entries (nothing supplied goes missing), it can
    be repaired deterministically by concatenating the extra text back onto the entry for
    the expected ID it immediately followed.
    """
    merged: list[dict] = []
    for entry in entries:
        if entry["id"] in expected_ids:
            merged.append(dict(entry))
        elif merged:
            merged[-1][text_key] = (
                f"{merged[-1][text_key].rstrip()} {entry[text_key].strip()}".strip()
            )
    return merged
