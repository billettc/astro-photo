"""Generate text with Grok — prefer local `grok -p` CLI, fall back to xAI API."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import httpx

from app.database import DEFAULT_DESCRIPTION_PROMPT

DEFAULT_MODEL = os.environ.get("GROK_MODEL", "grok-4.5")
GROK_TIMEOUT_S = int(os.environ.get("GROK_TIMEOUT_S", "180"))
DESCRIPTION_MD = "description.md"

_FILE_INSTRUCTION = (
    "\n\n---\n"
    "Work in the current directory. Research anything you need "
    "(distance, physical size, coordinates, etc.).\n"
    f"When you are finished, write the final gallery description ONLY to "
    f"`{DESCRIPTION_MD}` as Markdown. Do not leave the answer only in chat — "
    f"the application will load `{DESCRIPTION_MD}` when you are done."
)


def _find_grok_bin() -> Optional[str]:
    env = os.environ.get("GROK_BIN", "").strip()
    if env and Path(env).exists():
        return env
    which = shutil.which("grok")
    if which:
        return which
    for candidate in (
        Path.home() / ".grok" / "downloads" / "grok-linux-x86_64",
        Path("/usr/local/bin/grok"),
    ):
        if candidate.exists():
            return str(candidate)
    return None


def _read_description_md(workdir: Path) -> Optional[str]:
    path = workdir / DESCRIPTION_MD
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    # Strip accidental whole-doc fences
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1]).strip()
            if text.startswith("markdown"):
                text = text[len("markdown") :].lstrip()
    return text or None


def _via_cli_to_file(prompt: str) -> str:
    """
    Run Grok headless in a temp dir, ask it to research and write description.md,
    then load that file.
    """
    binary = _find_grok_bin()
    if not binary:
        raise RuntimeError("grok CLI not found")

    full_prompt = prompt.rstrip() + _FILE_INSTRUCTION

    with tempfile.TemporaryDirectory(prefix="astro-grok-") as tmp:
        workdir = Path(tmp)
        cmd = [
            binary,
            "-p",
            full_prompt,
            "--output-format",
            "plain",
            "--always-approve",
            "--no-subagents",
            "--max-turns",
            os.environ.get("GROK_MAX_TURNS", "8"),
            "--cwd",
            str(workdir),
        ]
        # Allow web research unless explicitly disabled
        if os.environ.get("GROK_DISABLE_WEB_SEARCH", "").lower() in {
            "1",
            "true",
            "yes",
        }:
            cmd.append("--disable-web-search")

        model = os.environ.get("GROK_MODEL")
        if model:
            cmd.extend(["-m", model])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GROK_TIMEOUT_S,
            cwd=str(workdir),
            env={**os.environ},
        )

        # Prefer the file Grok was asked to write
        for _ in range(10):
            text = _read_description_md(workdir)
            if text:
                return text
            time.sleep(0.15)

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(err or f"grok exited {result.returncode}")

        # Last resort: stdout (if the model ignored the file instruction)
        stdout = (result.stdout or "").strip()
        if stdout:
            return stdout
        raise RuntimeError(
            f"Grok finished but `{DESCRIPTION_MD}` was not created in the work directory"
        )


def _via_api(prompt: str) -> str:
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("XAI_API_KEY is not set")

    base = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1").rstrip("/")
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write album descriptions for a photo-sharing site. "
                    "Follow the user's instructions carefully. "
                    "Include distance and size when known. "
                    "You may use light Markdown. "
                    "Do not wrap the whole reply in a code fence or quotation marks."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
    }
    with httpx.Client(timeout=GROK_TIMEOUT_S) as client:
        resp = client.post(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    try:
        text = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError, TypeError) as exc:
        raise RuntimeError(f"Unexpected API response: {json.dumps(data)[:400]}") from exc
    if not text:
        raise RuntimeError("API returned an empty response")
    return text


def generate_text(prompt: str) -> str:
    """Try grok CLI (research → description.md) first, then xAI HTTP API."""
    errors: list[str] = []
    if _find_grok_bin():
        try:
            return _via_cli_to_file(prompt)
        except Exception as exc:  # noqa: BLE001 — fall through to API
            errors.append(f"cli: {exc}")
    try:
        return _via_api(prompt)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"api: {exc}")
    raise RuntimeError(
        "Could not reach Grok. "
        + " | ".join(errors)
        + " Ensure the grok CLI is available or set XAI_API_KEY."
    )


def format_photo_list(photo_names: list[str], limit: int = 24) -> str:
    names = photo_names[:limit]
    listed = "\n".join(f"- {n}" for n in names) if names else "- (no filenames yet)"
    if len(photo_names) > limit:
        listed += f"\n(+{len(photo_names) - limit} more photos)"
    return listed


def album_description_prompt(
    title: str,
    photo_names: list[str],
    template: str | None = None,
) -> str:
    """
    Build the user prompt from a configurable template.

    Placeholders:
      {title}  — album title
      {photos} — bullet list of photo filenames
    """
    base = (template or "").strip() or DEFAULT_DESCRIPTION_PROMPT
    photos = format_photo_list(photo_names)
    try:
        return base.format(title=title, photos=photos)
    except (KeyError, ValueError, IndexError):
        # Tolerate broken braces in a custom template — append context instead
        return (
            f"{base}\n\n"
            f'Album title: "{title}"\n'
            f"Photo filenames:\n{photos}\n"
            f"Output only the description."
        )
