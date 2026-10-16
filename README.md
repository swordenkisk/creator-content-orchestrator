# Creator Content Orchestrator

A starter service for analyzing creator-provided performance metrics, generating
content through ComfyUI, and routing drafts to Discord for human approval.

## Safety and scope

- Use only content you own, have licensed, or are explicitly authorized to use.
- This project does not scrape Instagram, Fanvue, or other creators' accounts.
- A human approval is required before a job becomes `approved_pending_publish`.
- Publishing adapters are not included. Add authorized platform integrations
  only after configuring their official access and credentials.

## Requirements

- Python 3.11+
- A ComfyUI instance
- A Discord application and bot, if Discord approvals are enabled
- An API-format ComfyUI workflow JSON file

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

Edit .env, set a strong REVIEW_TOKEN, and point
COMFYUI_WORKFLOW_PATH to your workflow file.

ComfyUI workflow placeholders
Export your workflow in API format. In the workflow JSON, replace the relevant
text values with these exact placeholders:

{{PROMPT}}
{{NEGATIVE_PROMPT}}
{{SOURCE_IMAGE}}
The source image filename must already be available in ComfyUI's input folder.
Use images you own or are authorized to process.

Run the API
From the repository root:

bash


PYTHONPATH=src uvicorn creatorflow.api:app --host 127.0.0.1 --port 8000
Run the Discord bot
Set DISCORD_BOT_TOKEN, CREATORFLOW_API_URL, and REVIEW_TOKEN in .env,
then run:

bash


PYTHONPATH=src python -m creatorflow.discord\_bot
The bot registers /review. Use it with a job ID to view a draft and approve
or reject it.

For Discord notifications on completed renders, configure
DISCORD_WEBHOOK_URL. The bot and API must be able to reach each other.

API examples
Create a job:

bash


curl -X POST http://127.0.0.1:8000/jobs \\
  -H 'Content-Type: application/json' \\
  -d '{
    "prompt": "A bright studio product photo, warm natural lighting",
    "negative\_prompt": "watermark, blurry, low quality",
    "caption": "New studio look.",
    "source\_image": "owned-product-photo.png"
  }'
Check a job:

bash


curl http://127.0.0.1:8000/jobs/JOB\_ID
Submit metrics for recommendations:

bash


curl -X POST http://127.0.0.1:8000/recommendations \\
  -H 'Content-Type: application/json' \\
  -d '{
    "records": [
      {
        "category": "tutorial",
        "reach": 1200,
        "likes": 80,
        "comments": 12,
        "saves": 25,
        "conversions": 9
      },
      {
        "category": "behind-the-scenes",
        "reach": 900,
        "likes": 60,
        "comments": 8,
        "saves": 18,
        "conversions": 4
      }
    ]
  }'
Status flow
queued → rendering → awaiting_review → approved_pending_publish

Rejected drafts move to rejected; failed renders move to failed.

Next integrations
Persist metrics from authorized API access or CSV imports.
Add account-specific publishing adapters using official platform access.
Add a scheduled worker and deployment configuration for your environment.
Add reviewer roles and audit logging before running this beyond a local MVP.