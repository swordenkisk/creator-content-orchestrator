from __future__ import annotations

import os

import discord
import httpx
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
API_BASE_URL = os.getenv("CREATORFLOW_API_URL", "http://127.0.0.1:8000")
REVIEW_TOKEN = os.getenv("REVIEW_TOKEN", "")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


class ReviewButtons(discord.ui.View):
    def __init__(self, job_id: str):
        super().__init__(timeout=3600)
        self.job_id = job_id

    async def submit_review(
        self,
        interaction: discord.Interaction,
        approved: bool,
    ) -> None:
        async with httpx.AsyncClient(timeout=15) as session:
            response = await session.post(
                f"{API_BASE_URL}/jobs/{self.job_id}/review",
                headers={"X-Review-Token": REVIEW_TOKEN},
                json={"approved": approved},
            )

        if response.is_success:
            label = "Approved" if approved else "Rejected"
            await interaction.response.edit_message(
                content=f"{label}: `{self.job_id}`",
                view=None,
            )
        else:
            await interaction.response.send_message(
                f"Review failed: {response.text[:500]}",
                ephemeral=True,
            )

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.submit_review(interaction, True)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.submit_review(interaction, False)


@tree.command(name="review", description="Review a generated content job.")
@app_commands.describe(job_id="The job ID returned by CreatorFlow")
async def review(interaction: discord.Interaction, job_id: str) -> None:
    async with httpx.AsyncClient(timeout=15) as session:
        response = await session.get(f"{API_BASE_URL}/jobs/{job_id}")

    if response.status_code == 404:
        await interaction.response.send_message("Job not found.", ephemeral=True)
        return
    if not response.is_success:
        await interaction.response.send_message(
            f"Could not load job: {response.text[:400]}",
            ephemeral=True,
        )
        return

    job = response.json()
    if job["status"] != "awaiting_review":
        await interaction.response.send_message(
            f"Job status is `{job['status']}`, not awaiting review.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="Generated draft",
        description=job["caption"] or "No caption provided.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Job ID", value=job_id, inline=False)
    if job["output_image"]:
        embed.add_field(name="Preview URL", value=job["output_image"], inline=False)

    await interaction.response.send_message(
        embed=embed,
        view=ReviewButtons(job_id),
        ephemeral=True,
    )


@client.event
async def on_ready() -> None:
    await tree.sync()
    print(f"Logged in as {client.user}")


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError("Set DISCORD_BOT_TOKEN in the environment.")
    client.run(BOT_TOKEN)
