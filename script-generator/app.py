"""Faceless YouTube script generator.

Serves a single-page UI and one streaming endpoint that turns a video idea
into a narration script, a character sheet, and per-scene image/video prompts.

Run:  uvicorn app:app --reload   (needs ANTHROPIC_API_KEY, or SCRIPTGEN_DEMO=1)
"""

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator, Literal

import anthropic
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

HERE = Path(__file__).parent
MODEL = os.environ.get("SCRIPTGEN_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("SCRIPTGEN_EFFORT", "medium")
DEMO = os.environ.get("SCRIPTGEN_DEMO") == "1"
WORDS_PER_MINUTE = 150

app = FastAPI(title="Faceless Script Generator")
client = anthropic.AsyncAnthropic() if not DEMO else None
NO_KEY = "Anthropic API key is missing or invalid. Set ANTHROPIC_API_KEY and restart."


class GenerateRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=2000)
    niche: str = Field(default="", max_length=100)
    duration_minutes: float = Field(default=5, ge=0.5, le=20)
    tone: str = Field(default="Engaging and conversational", max_length=100)
    visual_style: str = Field(default="Cinematic photorealistic", max_length=200)
    audience: str = Field(default="", max_length=200)
    language: str = Field(default="English", max_length=50)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    notes: str = Field(default="", max_length=2000)


def _obj(properties: dict) -> dict:
    """Object schema with every property required, as structured outputs need."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


STR = {"type": "string"}
STR_LIST = {"type": "array", "items": STR}

OUTPUT_SCHEMA = _obj(
    {
        "titles": STR_LIST,
        "hook": STR,
        "style_anchor": STR,
        "characters": {
            "type": "array",
            "items": _obj(
                {"name": STR, "role": STR, "appearance": STR, "image_prompt": STR}
            ),
        },
        "scenes": {
            "type": "array",
            "items": _obj(
                {
                    "number": {"type": "integer"},
                    "title": STR,
                    "narration": STR,
                    "duration_seconds": {"type": "integer"},
                    "characters": STR_LIST,
                    "image_prompt": STR,
                    "video_prompt": STR,
                    "on_screen_text": STR,
                }
            ),
        },
        "thumbnail_prompt": STR,
        "description": STR,
        "tags": STR_LIST,
    }
)

SYSTEM = """You write production packages for faceless YouTube channels: videos \
made of voiceover narration over AI-generated visuals, with no on-camera presenter.

The person using your output will paste the narration into a text-to-speech tool \
and paste each prompt, one at a time and with no other context, into an image \
generator and then an image-to-video generator. That shapes everything:

Narration is written for the ear. It gets read aloud by a synthetic voice, so it \
contains only the words to be spoken: no stage directions, speaker labels, \
bracketed cues, emoji or markdown. Open with a hook that earns the first fifteen \
seconds, keep tension or curiosity moving so viewers stay, and end with a payoff \
and a natural call to action. Be accurate: don't invent facts, statistics or \
quotes for non-fiction topics.

Every image and video prompt must stand completely alone, because the generator \
has no memory between prompts. Never write "the same man" or refer to a character \
by name only. Each time a character appears, repeat their full physical \
description word for word from the character sheet, so they look the same in \
every scene. Likewise, end every image prompt with the exact style_anchor text so \
the whole video shares one look. If the video needs no recurring characters (a \
pure documentary or explainer), return an empty character list and say so by \
leaving each scene's characters list empty.

Field guide:
- titles: three click-worthy but honest title options.
- hook: the opening lines of narration (also included in scene 1).
- style_anchor: one reusable phrase capturing art style, lighting, palette and \
camera feel, appended verbatim to every image prompt.
- characters[].appearance: a fixed, concrete description (age, build, face, hair, \
clothing, distinguishing features) reusable verbatim. characters[].image_prompt: \
a reference-sheet prompt for generating that character on a neutral background.
- scenes[].narration: the spoken words for this scene. Concatenating all scenes \
in order must give the complete script.
- scenes[].image_prompt: subject, action, setting, composition, lighting, then \
the style anchor. Describe what is visible; avoid text in the image.
- scenes[].video_prompt: how the still image should move: camera motion and \
subject motion for a clip of a few seconds.
- scenes[].on_screen_text: a short caption or keyword overlay, or an empty string.
- scenes[].characters: names from the character sheet appearing in the scene.
- thumbnail_prompt: one striking image prompt for the thumbnail.
- description and tags: YouTube description (with a one-line summary first) and \
search tags."""


def build_user_prompt(req: GenerateRequest) -> str:
    words = round(req.duration_minutes * WORDS_PER_MINUTE)
    lines = [
        f"Video idea: {req.topic}",
        f"Channel niche: {req.niche or 'not specified'}",
        f"Target length: about {req.duration_minutes:g} minutes of narration "
        f"(roughly {words} words in total across all scenes).",
        f"Tone: {req.tone}",
        f"Visual style: {req.visual_style}",
        f"Aspect ratio: {req.aspect_ratio} "
        f"({'vertical short-form' if req.aspect_ratio == '9:16' else 'compose for this frame'})",
        f"Audience: {req.audience or 'general YouTube viewers'}",
        f"Language for narration, titles, description and on-screen text: {req.language}. "
        "Write image and video prompts in English, since generators follow English best.",
        "Pacing: start a new scene whenever the visual should change, about every "
        "two or three sentences (8 to 15 seconds), so the screen never sits still for long.",
    ]
    if req.notes:
        lines.append(f"Extra instructions from the creator: {req.notes}")
    return "\n".join(lines)


def _event(kind: str, **payload) -> str:
    return json.dumps({"type": kind, **payload}) + "\n"


async def _demo_stream() -> AsyncIterator[str]:
    text = (HERE / "demo_output.json").read_text()
    for i in range(0, len(text), 400):
        yield _event("delta", text=text[i : i + 400])
        await asyncio.sleep(0.05)
    yield _event("done", data=json.loads(text))


async def _generate(req: GenerateRequest) -> AsyncIterator[str]:
    """Yield newline-delimited JSON events: delta*, then done or error."""
    if DEMO:
        async for chunk in _demo_stream():
            yield chunk
        return

    try:
        async with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_user_prompt(req)}],
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            # If a safety classifier declines, re-run on a fallback model
            # instead of failing the request.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            async for text in stream.text_stream:
                yield _event("delta", text=text)
            message = await stream.get_final_message()

        if message.stop_reason == "refusal":
            yield _event(
                "error",
                message="The model declined this topic. Try rephrasing the idea.",
            )
            return
        if message.stop_reason == "max_tokens":
            yield _event(
                "error",
                message="The script was too long to finish. Try a shorter video length.",
            )
            return

        text = "".join(b.text for b in message.content if b.type == "text")
        yield _event("done", data=json.loads(text))

    except anthropic.AuthenticationError:
        yield _event("error", message=NO_KEY)
    except anthropic.RateLimitError:
        yield _event(
            "error", message="Rate limited by the API. Wait a moment and try again."
        )
    except anthropic.APIStatusError as e:
        yield _event("error", message=f"API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        yield _event("error", message="Could not reach the Anthropic API.")
    except TypeError:
        # Raised by the SDK when no credentials can be resolved at all.
        yield _event("error", message=NO_KEY)
    except json.JSONDecodeError:
        yield _event(
            "error", message="The model returned an incomplete result. Try again."
        )


@app.post("/api/generate")
async def generate(req: GenerateRequest) -> StreamingResponse:
    return StreamingResponse(
        _generate(req),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/config")
async def config() -> dict:
    return {"demo": DEMO, "model": MODEL}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
