from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

APP_DIR = Path(__file__).resolve().parents[2]
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "creatorflow.db"))
COMFYUI_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
WORKFLOW_PATH = Path(os.getenv("COMFYUI_WORKFLOW_PATH", "workflows/workflow_api.json"))
POLL_SECONDS = float(os.getenv("COMFYUI_POLL_SECONDS", "2"))
TIMEOUT_SECONDS = int(os.getenv("COMFYUI_TIMEOUT_SECONDS", "300"))
REVIEW_TOKEN = os.getenv("REVIEW_TOKEN", "")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_REVIEW_CHANNEL_ID", "")

app = FastAPI(title="Creator Content Orchestrator", version="0.1.0")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db_connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                prompt TEXT NOT NULL,
                negative_prompt TEXT NOT NULL,
                caption TEXT NOT NULL,
                source_image TEXT NOT NULL,
                output_image TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


initialize_database()


class MetricRecord(BaseModel):
    category: str = Field(min_length=1, max_length=100)
    reach: int = Field(ge=0)
    likes: int = Field(ge=0)
    comments: int = Field(ge=0)
    saves: int = Field(ge=0)
    conversions: int = Field(ge=0)


class RecommendationRequest(BaseModel):
    records: list[MetricRecord] = Field(min_length=1)


class CreateJobRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = Field(default="", max_length=4000)
    caption: str = Field(default="", max_length=2200)
    # Filename must already exist in ComfyUI's configured input directory.
    source_image: str = Field(min_length=1, max_length=255)


class ReviewRequest(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=1000)


def require_review_token(token: str | None) -> None:
    if not REVIEW_TOKEN:
        raise HTTPException(status_code=503, detail="Review token is not configured.")
    if not token or not secrets.compare_digest(token, REVIEW_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid review token.")


def get_job(job_id: str) -> dict[str, Any]:
    with db_connect() as connection:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return dict(row)


def update_job(job_id: str, **fields: str) -> None:
    fields["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = [*fields.values(), job_id]
    with db_connect() as connection:
        connection.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?",
            values,
        )


def replace_placeholders(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: replace_placeholders(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [replace_placeholders(item, replacements) for item in value]
    if isinstance(value, str):
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, replacement)
        return value
    return value


def find_first_image(history_entry: dict[str, Any]) -> dict[str, str] | None:
    outputs = history_entry.get("outputs", {})
    for node_output in outputs.values():
        for image in node_output.get("images", []):
            if image.get("filename"):
                return {
                    "filename": str(image["filename"]),
                    "subfolder": str(image.get("subfolder", "")),
                    "type": str(image.get("type", "output")),
                }
    return None


async def notify_discord(job_id: str, caption: str) -> None:
    channel_id = DISCORD_CHANNEL_ID
    if not channel_id:
        return

    # The bot provides the interactive /review command.
    # Discord notification delivery is handled by discord_bot.py.
    bot_url = os.getenv("CREATORFLOW_PUBLIC_URL", "http://127.0.0.1:8000")
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        return

    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            webhook_url,
            json={
                "content": (
                    f"Draft ready for review: `{job_id}`\n"
                    f"Caption: {caption or '(no caption)'}\n"
                    f"Open the Discord bot and run `/review job_id:{job_id}`.\n"
                    f"Service: {bot_url}"
                )
            },
        )


async def render_job(job_id: str) -> None:
    job = get_job(job_id)
    update_job(job_id, status="rendering", error="")

    if not WORKFLOW_PATH.is_file():
        update_job(
            job_id,
            status="failed",
            error=f"Workflow file not found: {WORKFLOW_PATH}",
        )
        return

    try:
        workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        workflow = replace_placeholders(
            workflow,
            {
                "{{PROMPT}}": job["prompt"],
                "{{NEGATIVE_PROMPT}}": job["negative_prompt"],
                "{{SOURCE_IMAGE}}": job["source_image"],
            },
        )

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{COMFYUI_URL}/prompt",
                json={"prompt": workflow},
            )
            response.raise_for_status()
            prompt_id = response.json()["prompt_id"]

            deadline = asyncio.get_running_loop().time() + TIMEOUT_SECONDS
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(POLL_SECONDS)
                history_response = await client.get(
                    f"{COMFYUI_URL}/history/{prompt_id}"
                )
                history_response.raise_for_status()
                history = history_response.json().get(prompt_id, {})
                image = find_first_image(history)
                if image:
                    image_url = (
                        f"{COMFYUI_URL}/view"
                        f"?filename={image['filename']}"
                        f"&subfolder={image['subfolder']}"
                        f"&type={image['type']}"
                    )
                    update_job(
                        job_id,
                        status="awaiting_review",
                        output_image=image_url,
                    )
                    await notify_discord(job_id, job["caption"])
                    return

        update_job(job_id, status="failed", error="ComfyUI render timed out.")
    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc)[:1000])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/recommendations")
def recommendations(request: RecommendationRequest) -> dict[str, Any]:
    grouped: dict[str, list[MetricRecord]] = {}
    for record in request.records:
        grouped.setdefault(record.category, []).append(record)

    results = []
    for category, records in grouped.items():
        reach = sum(item.reach for item in records)
        interactions = sum(item.likes + item.comments + item.saves for item in records)
        conversions = sum(item.conversions for item in records)

        engagement_rate = interactions / max(reach, 1)
        conversion_rate = conversions / max(reach, 1)
        score = 0.6 * engagement_rate + 0.4 * conversion_rate

        results.append(
            {
                "category": category,
                "posts": len(records),
                "reach": reach,
                "engagement_rate": round(engagement_rate, 5),
                "conversion_rate": round(conversion_rate, 5),
                "score": round(score, 5),
            }
        )

    results.sort(key=lambda item: item["score"], reverse=True)
    return {
        "recommended_categories": results[:3],
        "method": "0.6 * engagement rate + 0.4 * conversion rate",
        "note": "Use comparable reporting periods and metric definitions.",
    }


@app.post("/jobs", status_code=202)
async def create_job(
    request: CreateJobRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    # Prevent path traversal; the image must be staged in ComfyUI's input folder.
    if Path(request.source_image).name != request.source_image:
        raise HTTPException(status_code=400, detail="Use a filename, not a path.")

    job_id = str(uuid.uuid4())
    timestamp = utc_now()
    with db_connect() as connection:
        connection.execute(
            """
            INSERT INTO jobs (
                id, status, prompt, negative_prompt, caption,
                source_image, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                "queued",
                request.prompt,
                request.negative_prompt,
                request.caption,
                request.source_image,
                timestamp,
                timestamp,
            ),
        )

    background_tasks.add_task(render_job, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
def read_job(job_id: str) -> dict[str, Any]:
    return get_job(job_id)


@app.post("/jobs/{job_id}/review")
def review_job(
    job_id: str,
    request: ReviewRequest,
    x_review_token: str | None = Header(default=None),
) -> dict[str, str]:
    require_review_token(x_review_token)
    job = get_job(job_id)
    if job["status"] != "awaiting_review":
        raise HTTPException(
            status_code=409,
            detail=f"Job is not awaiting review; current status: {job['status']}.",
        )

    status = "approved_pending_publish" if request.approved else "rejected"
    update_job(job_id, status=status, error=request.note)
    return {"job_id": job_id, "status": status}
