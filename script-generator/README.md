# Faceless Script Studio

A small web app for faceless YouTube channels. Describe a video idea and get:

- a voiceover script, split into scenes and written to be read aloud by TTS
- a character sheet with a fixed appearance for each recurring character, plus a
  reference-image prompt
- an image prompt and a video (motion) prompt for every scene, each one
  self-contained so characters and art style stay consistent across generators
- title options, a thumbnail prompt, a description and tags

Results can be copied field by field, copied in bulk (all image prompts, the
full script), or exported as Markdown or JSON. Recent generations are kept in
the browser's local storage.

## Run it

```bash
cd script-generator
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn app:app --reload
```

Open http://127.0.0.1:8000.

To try the interface without an API key, run in demo mode, which returns a
bundled sample instead of calling the model:

```bash
SCRIPTGEN_DEMO=1 uvicorn app:app --reload
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | none | Claude API key (required unless in demo mode) |
| `SCRIPTGEN_MODEL` | `claude-opus-5-5` | Model used for generation |
| `SCRIPTGEN_EFFORT` | `medium` | `low`, `medium`, `high`, `xhigh` or `max`: higher is more thorough, slower and costlier |
| `SCRIPTGEN_DEMO` | unset | Set to `1` for sample output with no API calls |

## How it works

- `app.py` is a FastAPI server. `POST /api/generate` sends the form to Claude
  with a JSON schema (structured outputs), streams the response back as
  newline-delimited JSON events (`delta`, then `done` or `error`), and the page
  shows progress as scenes are written.
- `static/index.html` is the whole front end: plain HTML, CSS and JavaScript,
  no build step.
- Requests opt in to server-side refusal fallbacks, so if the primary model's
  safety classifier declines a topic the API retries on a fallback model.

The API key stays on the server. There is no login or rate limiting, so put
those in front of it before exposing it to the public internet.
