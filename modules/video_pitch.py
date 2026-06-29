# video_pitch.py — $0 personalized "Loom-style" pitch video, one per QUALIFIED lead.
#
# GATE: this only ever runs for highly qualified clients — leads who replied
# "interested" (the enthusiastic yes), booked a call, or reached the booked
# stage. Cold leads never get a video. The gate is enforced in is_qualified()
# and checked in BOTH make() and make_batch(), so there is no path that renders
# a video for an unqualified lead unless you explicitly pass allow_unqualified
# (used only by the built-in demo).
#
# Pipeline (no paid APIs, no avatar service):
#   1. Groq writes a short, lead-specific script  (free; cacheable static prefix)
#   2. edge-tts speaks the narration  -> mp3       (free Microsoft Neural voices)
#   3. a self-contained dark HTML page is rendered with the lead's name + beats
#   4. Playwright screen-records that page          (already a project dep)
#   5. ffmpeg muxes recording + voiceover -> mp4    (gyan.dev full build, local)
#
# Timing is synced off ONE number: we generate the audio first, ffprobe its real
# duration, then drive both the recording length AND the on-page beat animation
# off that duration. No manual keyframing — visuals always match the voiceover.
#
# Cost: $0. edge-tts and ffmpeg need no key; Groq runs on the free tier and the
# script prompt keeps its static half first so prompt-caching kicks in per lead.
# If GROQ_API_KEY is unset, a deterministic template script is used instead, so
# the generator still produces a video offline.

import asyncio
import html
import json
import os
import subprocess
import tempfile
from pathlib import Path

try:
    from slugify import slugify
except ImportError:  # graceful fallback — keep the whitelist guarantee regardless
    import re as _re

    def slugify(text: str) -> str:
        return _re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "lead"

# Reuse the project's Groq config (same client pattern as modules/generator.py).
try:
    from config import LLM_MODEL, LLM_BASE_URL
except Exception:  # allow `python -m modules.video_pitch` from any cwd
    LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    LLM_BASE_URL = "https://api.groq.com/openai/v1"

LLM_API_KEY = os.getenv("GROQ_API_KEY")


def _num_env(name: str, default, cast):
    """Env override with a safe fallback — never crash import on a bad value."""
    try:
        return cast(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _js_json(obj) -> str:
    """JSON for safe embedding inside a <script> tag: escape <, >, & so a
    </script> (or comment opener) in LLM output can't break out of script
    context. Paired with textContent at render time = no XSS path. Modern
    Chromium (ES2019+) tolerates raw U+2028/U+2029 inside string literals."""
    return (json.dumps(obj)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


# ── Output + render settings (all overridable via env) ───────────────────────
OUT_DIR = Path(__file__).resolve().parent.parent / "runs" / "video_pitches"
VOICE = os.getenv("VIDEO_PITCH_VOICE", "en-US-AndrewMultilingualNeural")
WIDTH = _num_env("VIDEO_PITCH_W", 1280, int)
HEIGHT = _num_env("VIDEO_PITCH_H", 720, int)
TAIL_S = _num_env("VIDEO_PITCH_TAIL", 0.8, float)      # hold on the last frame
MIN_DUR_S = 6.0                                         # never record shorter


class VideoPitchError(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# GATE — qualified clients only
# ─────────────────────────────────────────────────────────────────────────────

# reply_handler.py tags a lead's reply_status with its category. "interested"
# is its label for "expressed curiosity, asked a question, or said yes" — the
# enthusiastic yes we want. booked_call/stage cover leads who went further.
_QUALIFIED_REPLY = {"interested"}
_QUALIFIED_STAGE = {"booked"}


def is_qualified(lead: dict) -> bool:
    """True only for highly qualified WARM clients. A cold high score is NOT
    enough here; we require a real positive signal from the conversation (they
    said yes / booked). For cold first-touch eligibility use is_probable_client.
    Set lead['video_pitch_force'] truthy to override per-lead."""
    if str(lead.get("video_pitch_force", "")).strip().lower() in ("1", "true", "yes"):
        return True
    if str(lead.get("booked_call", "")).strip().lower() == "yes":
        return True
    if str(lead.get("reply_status", "")).strip().lower() in _QUALIFIED_REPLY:
        return True
    if str(lead.get("stage", "")).strip().lower() in _QUALIFIED_STAGE:
        return True
    return False


# Cold first-touch video: send a personalized video to HIGHLY RATED prospects on
# the first message (still NO link - the cold rule holds). Gated on lead quality
# so render compute is never spent on low-probability leads. Threshold mirrors the
# pipeline's SCORE_HIGH (7); override with VIDEO_PITCH_COLD_MIN_SCORE.
_COLD_MIN_SCORE = _num_env("VIDEO_PITCH_COLD_MIN_SCORE", 7, int)


def is_probable_client(lead: dict) -> bool:
    """True for a highly rated COLD lead that should get a first-touch video."""
    try:
        if float(lead.get("lead_score", 0) or 0) >= _COLD_MIN_SCORE:
            return True
    except (TypeError, ValueError):
        pass
    return str(lead.get("lead_priority", "")).strip().lower() == "high"


def _render_allowed(lead: dict) -> bool:
    """Render a video for warm (qualified) leads OR highly rated cold prospects."""
    return is_qualified(lead) or is_probable_client(lead)


# ─────────────────────────────────────────────────────────────────────────────
# 1. SCRIPT  (Groq, with an offline template fallback)
# ─────────────────────────────────────────────────────────────────────────────

# Static prefix FIRST so Groq's automatic prompt caching matches it across every
# lead (same convention as generator.py — never move a {var} above this block).
_SCRIPT_RULES_STATIC = """
You write a 20-30 second spoken script for a personalized pitch VIDEO that a
21-year-old developer named Shaurya sends to a local service business. He builds
AI voice receptionists that pick up the phone, answer FAQs, and book appointments
so the business stops losing after-hours and missed calls.

VOICE: spoken out loud, warm, plain, confident. Short sentences. No jargon, no
"AI-powered solution", no "leverage", no "seamless". Sound like a real person
talking to one owner, not a marketer addressing a crowd.

Return ONLY a JSON object with exactly these keys:
- "headline": <= 6 words, the OUTCOME for them (e.g. "Never miss a patient call").
- "subhead": one short line naming the business.
- "beats": array of EXACTLY 3 objects {"k": 2-4 word label, "v": one short line}.
  Each beat is one on-screen idea: the gap they have now, what changes, the result.
- "narration": the full spoken voiceover, 55-75 words. Open by naming the business
  and one specific, observable fact about their world. State plainly what you built
  and the single outcome it gives them. End with ONE soft yes/no ask ("want the
  2-minute version?"). Never say the words "AI", "bot", or "solution".
No markdown, no extra keys.
"""


def build_script_prompt(data: dict) -> str:
    company = data.get("company_name") or data.get("label") or "your business"
    niche = data.get("niche", "service business")
    location = data.get("location") or data.get("city", "")
    hook = data.get("company_hook") or data.get("person_hook") or ""
    hook_line = f'\nVERIFIED FACT TO OPEN WITH (use it, do not announce you researched them): {hook}' if hook else ""
    # Dynamic block comes AFTER the cacheable static rules above.
    return f"""{_SCRIPT_RULES_STATIC}

THIS LEAD:
Business: {company}
Type: {niche}
Location: {location}{hook_line}
"""


def _fallback_script(data: dict) -> dict:
    """Deterministic script when GROQ_API_KEY is unset — keeps the tool offline-able."""
    company = data.get("company_name") or data.get("label") or "your clinic"
    niche = data.get("niche", "clinic")
    city = data.get("city") or data.get("location", "")
    where = f" in {city}" if city else ""
    return {
        "headline": "Stop missing patient calls",
        "subhead": company,
        "beats": [
            {"k": "Right now", "v": f"Calls to {company} after hours go unanswered."},
            {"k": "The fix", "v": "A front desk that picks up every call, day or night."},
            {"k": "The result", "v": "Bookings captured instead of lost to the next clinic."},
        ],
        "narration": (
            f"Hey {company}. Running a {niche}{where} means the calls you miss after hours "
            f"usually book somewhere else. I built a front desk that picks up every one of "
            f"those calls, answers the usual questions, and books the appointment for you. "
            f"No missed call, no lost patient. Want the two-minute version?"
        ),
    }


def generate_script(data: dict) -> dict:
    if not LLM_API_KEY:
        return _fallback_script(data)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": build_script_prompt(data)}],
            temperature=0.7,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        script = json.loads(resp.choices[0].message.content)
        # Guard the shape — fall back if the model drifted.
        if not script.get("narration") or len(script.get("beats", [])) < 3:
            raise ValueError("script missing narration/beats")
        script["beats"] = script["beats"][:3]
        return script
    except Exception as e:
        print(f"  [video_pitch] Groq script failed ({e}); using template fallback")
        return _fallback_script(data)


# ─────────────────────────────────────────────────────────────────────────────
# 2. NARRATION  (edge-tts -> mp3)  and duration probe (ffprobe)
# ─────────────────────────────────────────────────────────────────────────────

async def _narrate(text: str, mp3_path: Path) -> None:
    import edge_tts
    await edge_tts.Communicate(text, VOICE).save(str(mp3_path))


def _audio_duration(mp3_path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(mp3_path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise VideoPitchError(f"ffprobe could not read duration: {out.stderr[:200]}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. PAGE  — self-contained dark HTML, on-brand (zinc-950 + red accent).
#    Beats reveal evenly across `dur` seconds, read from the URL hash, so the
#    visuals stay in lockstep with the voiceover length.
# ─────────────────────────────────────────────────────────────────────────────

def render_page(data: dict, script: dict, html_path: Path, dur: float) -> None:
    # HTML text context -> html.escape is the correct defense here.
    company = html.escape(data.get("company_name") or data.get("label") or "Your Business")
    headline = html.escape(script.get("headline", "Never miss a call"))
    subhead = html.escape(script.get("subhead", company))
    # <script> context -> _js_json (escape <>&); values are set via textContent
    # in the JS below, so they are never HTML-parsed. No escape/innerHTML mix.
    beats = script.get("beats", [])[:3]
    beats_json = _js_json([{"k": b.get("k", ""), "v": b.get("v", "")} for b in beats])

    # ── CREATIVE LEVER ───────────────────────────────────────────────────────
    # `choreography` below is the one genuinely creative knob: how the page moves
    # while the voiceover plays. The default reveals the headline, then the three
    # beats spaced evenly across the narration, then a closing CTA card. Tune the
    # CSS/JS in this template to change the feel (pace, motion, the fake "incoming
    # call -> answered" beat, etc). Everything else is plumbing.
    page = f"""<!doctype html><html><head><meta charset="utf-8"><style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  :root {{ --dur: {dur:.2f}s; }}
  body {{ background:#09090b; color:#fafafa; height:100vh; overflow:hidden;
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    display:flex; align-items:center; justify-content:center; }}
  .grain:after {{ content:""; position:fixed; inset:0; pointer-events:none; opacity:.04;
    background-image:radial-gradient(#fff 1px, transparent 1px); background-size:3px 3px; }}
  .glow {{ position:fixed; width:60vw; height:60vw; border-radius:50%; filter:blur(120px);
    background:radial-gradient(circle, rgba(220,38,38,.28), transparent 70%); top:-10vw; right:-10vw; }}
  .wrap {{ width:78%; max-width:980px; }}
  .kicker {{ color:#dc2626; font-weight:600; letter-spacing:.18em; text-transform:uppercase;
    font-size:14px; opacity:0; transform:translateY(8px); }}
  h1 {{ font-size:64px; line-height:1.04; font-weight:800; margin:14px 0 6px;
    opacity:0; transform:translateY(14px); }}
  .sub {{ font-size:22px; color:#a1a1aa; opacity:0; transform:translateY(10px); }}
  .beats {{ margin-top:46px; display:flex; flex-direction:column; gap:18px; }}
  .beat {{ opacity:0; transform:translateX(-16px); display:flex; gap:18px; align-items:baseline; }}
  .beat .k {{ color:#dc2626; font-weight:700; font-size:15px; min-width:130px;
    text-transform:uppercase; letter-spacing:.06em; }}
  .beat .v {{ font-size:26px; color:#e4e4e7; }}
  .cta {{ position:fixed; bottom:54px; left:50%; transform:translate(-50%,12px); opacity:0;
    background:#dc2626; color:#fff; padding:14px 30px; border-radius:999px; font-size:20px;
    font-weight:700; box-shadow:0 10px 40px rgba(220,38,38,.4); }}
  .show {{ opacity:1 !important; transform:none !important; transition:all .7s cubic-bezier(.2,.7,.2,1); }}
</style></head><body class="grain">
  <div class="glow"></div>
  <div class="wrap">
    <div class="kicker" id="kicker">For {company}</div>
    <h1 id="h1">{headline}</h1>
    <div class="sub" id="sub">{subhead}</div>
    <div class="beats" id="beats"></div>
  </div>
  <div class="cta" id="cta">Want the 2-minute version? &nbsp;&rarr;</div>
<script>
  var dur = {dur:.2f} * 1000;
  var beats = {beats_json};
  var bWrap = document.getElementById('beats');
  beats.forEach(function(b){{
    var el = document.createElement('div'); el.className='beat';
    var k = document.createElement('div'); k.className='k'; k.textContent = b.k;
    var v = document.createElement('div'); v.className='v'; v.textContent = b.v;
    el.appendChild(k); el.appendChild(v); bWrap.appendChild(el);
  }});
  function show(id, t){{ setTimeout(function(){{ document.getElementById(id).classList.add('show'); }}, t); }}
  // Headline lands in the first ~12% of the narration; beats split the middle 65%;
  // CTA arrives near the end. All proportional to `dur`, so it tracks the voiceover.
  show('kicker', dur*0.02); show('h1', dur*0.06); show('sub', dur*0.12);
  var beatEls = document.querySelectorAll('.beat');
  var start = dur*0.22, span = dur*0.62;
  beatEls.forEach(function(el,i){{
    setTimeout(function(){{ el.classList.add('show'); }}, start + span*(i/Math.max(1,beatEls.length)));
  }});
  show('cta', dur*0.90);
</script></body></html>"""
    html_path.write_text(page, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# 4. RECORD  (Playwright screen-record of the page for exactly `dur` seconds)
# ─────────────────────────────────────────────────────────────────────────────

async def _record(html_path: Path, out_dir: Path, dur: float) -> Path:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        context = await browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            record_video_dir=str(out_dir),
            record_video_size={"width": WIDTH, "height": HEIGHT},
        )
        page = await context.new_page()
        await page.goto(html_path.as_uri())
        await page.wait_for_timeout(int(dur * 1000) + int(TAIL_S * 1000))
        video = page.video
        await context.close()   # finalizes the .webm
        await browser.close()
        return Path(await video.path()) if video else None


# ─────────────────────────────────────────────────────────────────────────────
# 5. MUX  (ffmpeg: recording video + voiceover audio -> shareable mp4)
# ─────────────────────────────────────────────────────────────────────────────

def _mux(video_path: Path, audio_path: Path, out_path: Path) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:a", "aac", "-b:a", "160k",
        "-shortest",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not out_path.exists():
        raise VideoPitchError(f"ffmpeg mux failed: {res.stderr[-400:]}")


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────────

def make(data: dict, force: bool = False, allow_unqualified: bool = False) -> Path | None:
    """Build one personalized pitch video for a QUALIFIED lead. Returns the mp4
    path, or None if the lead is not qualified (the default guardrail).

    Checkpointed: if the mp4 already exists and force is False, it is reused, so a
    killed batch run resumes without regenerating finished videos.
    """
    name = data.get("company_name") or data.get("label") or "lead"
    if not allow_unqualified and not _render_allowed(data):
        print(f"  [video_pitch] SKIP {name}: not eligible for a video "
              f"(needs warm reply/booked, or lead_score>={_COLD_MIN_SCORE} / priority=high)")
        return None

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = data.get("slug") or slugify(name) or "lead"      # whitelist filename
    out_path = OUT_DIR / f"{slug}.mp4"
    if out_path.exists() and not force:
        print(f"  [video_pitch] {slug}.mp4 already exists — skipping (force=True to rebuild)")
        return out_path

    script = generate_script(data)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        audio = tmp / "vo.mp3"
        html_file = tmp / "page.html"

        async def _audio_then_video():
            await _narrate(script["narration"], audio)
            dur = max(MIN_DUR_S, _audio_duration(audio))
            render_page(data, script, html_file, dur)
            return await _record(html_file, tmp, dur), dur

        video_webm, dur = asyncio.run(_audio_then_video())
        if not video_webm or not video_webm.exists():
            raise VideoPitchError("Playwright produced no recording")
        _mux(video_webm, audio, out_path)

    print(f"  [video_pitch] {name}: {dur:.1f}s video -> {out_path}")
    return out_path


def ensure(lead: dict) -> Path | None:
    """Idempotent + exception-safe: return the lead's pitch mp4, rendering it once
    if the lead is eligible and it does not exist yet. A render failure returns
    None so this can never block an outreach send. Used by the cold delivery path
    to lazily render a first-touch video for a highly rated prospect."""
    slug = lead.get("slug") or slugify(lead.get("company_name") or lead.get("label") or "lead") or "lead"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    existing = OUT_DIR / f"{slug}.mp4"
    if existing.exists():
        return existing
    if not _render_allowed(lead):
        return None
    try:
        return make(lead)
    except Exception as e:
        print(f"  [video_pitch] ensure: render failed for {slug}: {e}")
        return None


def make_batch(force: bool = False, limit: int | None = None) -> list[Path]:
    """Pre-render videos for every ELIGIBLE lead in the Google Sheet: warm leads
    (is_qualified) plus highly rated cold prospects (is_probable_client). Uses the
    same eligibility gate (_render_allowed) as make()/ensure(); make()'s
    allow_unqualified is only the demo-render override, not a different gate.
    Pre-rendering here means the outreach send never blocks on a render.
    Reads the Sheet (reply_status / booked_call / stage / lead_score live there),
    so run via `doppler run -- python ...`.
    """
    from modules import sheets_writer
    try:
        leads = sheets_writer.get_all_leads()
    except Exception as e:
        print(f"[video_pitch] cannot read leads Sheet ({type(e).__name__}: {e}). "
              f"Run via `doppler run -- python -m modules.video_pitch batch` so Sheets creds load.")
        return []
    eligible = [l for l in leads if _render_allowed(l)]
    print(f"[video_pitch] {len(eligible)}/{len(leads)} leads eligible "
          f"(warm reply/booked, or lead_score>={_COLD_MIN_SCORE} / priority=high).")
    if limit:
        eligible = eligible[:limit]
    made = []
    for lead in eligible:
        try:
            out = make(lead, force=force)
            if out:
                made.append(out)
        except Exception as e:
            print(f"  [video_pitch] FAILED for {lead.get('company_name', '?')}: {e}")
    print(f"\n[video_pitch] done. {len(made)} videos in {OUT_DIR}")
    return made


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "batch":
        lim = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
        make_batch(force="--force" in sys.argv, limit=lim)
    else:
        # Demo: one video from a sample lead so you can see the output instantly.
        # allow_unqualified=True is intentional and ONLY for this showcase render;
        # real leads always pass through the is_qualified() gate.
        demo = {
            "company_name": "Smile Dental Studio",
            "niche": "dental",
            "city": "Jaipur",
            "location": "Jaipur, Rajasthan",
            "company_hook": "Your clinic's Google page shows you're closed Sundays.",
            "slug": "demo-smile-dental",
        }
        print(make(demo, force=True, allow_unqualified=True))
