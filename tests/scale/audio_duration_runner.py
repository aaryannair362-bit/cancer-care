"""
Standalone (non-pytest) resumable runner that measures how backend/app/sarvam_batch_transcriber.py
and the downstream scribe.scribe_transcript() pipeline actually behave across a real spread of
consultation durations (5-60 minutes), against the REAL live Sarvam Batch STT and Groq APIs --
built to answer a specific production report: "10-12 min consultations worked, 15+ min did not,
and now even normal-length OPD is failing."

Why this exists separately from tests/scale/runner.py: that harness deliberately mocks
scribe.transcribe_audio (see its own docstring) -- it was built to validate the text-extraction
pipeline (drug interactions, discharge summaries, note structuring) at volume, not real ASR. It
has no way to reproduce a duration-dependent Sarvam/Groq audio failure because it never sends
real audio anywhere. This script is the complement: it exercises the REAL audio leg end-to-end
(POST /api/transcribe-audio -> real Sarvam Batch STT -> real scribe.scribe_transcript() -> real
Groq), and mocks nothing except the auth/DB layer needed to safely run outside a browser.

Content: never fabricates clinical dialogue. Source text is pulled from two REAL, already-
reviewed sources already in this repo -- data/Test Cases*.pdf (9 long-form Hinglish consultation
transcripts, purpose-authored for transcription testing) and tests/scenarios/curated_use_cases.py
(22 multilingual OPD transcripts). A target duration longer than one real segment's own spoken
length is reached by concatenating multiple real segments (deterministically shuffled per
case_id, so a re-run with --resume reproduces the same case) -- never by inventing new medical
content. Every generated audio file and the transcript/scribe result for every case is saved to
disk under RESULTS_DIR for later reference, exactly as requested.

Audio synthesis: pyttsx3 (offline, Windows SAPI5 -- no network dependency, no per-call cost)
renders text to WAV; imageio_ffmpeg's bundled ffmpeg binary then re-encodes to WebM/Opus at
24000 bps mono -- the EXACT bitrate frontend/js/voice-capture.js's AUDIO_BITS_PER_SECOND uses in
production (see that file's comment for why 24kbps was chosen) -- so a generated case's byte
size faithfully matches what a real browser recording of the same duration would produce,
including whether it would trip MAX_AUDIO_UPLOAD_BYTES. A raw pyttsx3 WAV is ~40x bigger per
minute than this and would trip that cap at a completely different (wrong) duration than a real
recording ever would, making the whole test unrepresentative if skipped.

Safety: always runs against a throwaway SQLite database and a real listening uvicorn server on
localhost, never the production Postgres URL or the live Render deployment -- same assertion
tests/scale/runner.py already makes, copied verbatim.

Usage:
    python -m tests.scale.audio_duration_runner --smoke 6      # one real case per duration bucket
    python -m tests.scale.audio_duration_runner --total-cases 500
    python -m tests.scale.audio_duration_runner --resume       # (default) skip case_ids already done
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(REPO_ROOT))

RESULTS_DIR = Path(__file__).resolve().parent / "results" / "audio_duration"
AUDIO_DIR = RESULTS_DIR / "audio"
TRANSCRIPT_DIR = RESULTS_DIR / "transcripts"
for d in (RESULTS_DIR, AUDIO_DIR, TRANSCRIPT_DIR):
    d.mkdir(parents=True, exist_ok=True)
RESULTS_PATH = RESULTS_DIR / "results.jsonl"

# Realistic OPD-length distribution: most real consultations are short, long ones are the rarer
# tail -- matches how doctors actually spend time, not a uniform spread across 5-60.
DURATION_BUCKETS_MINUTES = [5, 8, 10, 12, 15, 18, 20, 25, 30, 40, 50, 60]
DURATION_WEIGHTS =        [3, 3, 3,  2,  2,  2,  2,  1,  1,  1,  1,  1]

WORDS_PER_MINUTE = 125  # measured live via this same pyttsx3 voice against the real content
# pool's actual doctor/patient turn-taking structure (122-133 wpm across two real samples) --
# NOT the ~176 wpm a single clean, unpunctuated paragraph measured at during initial calibration.
# The gap is real, not noise: SAPI5 pauses at each speaker-turn label ("Doctor:"/"Patient:"),
# which is correct/realistic behavior for dialogue, not a bug to fix away. This is only a
# targeting estimate anyway -- run_one_case always records the REAL measured
# actual_audio_duration_sec from the synthesized file, never this estimate.


# ---------------------------------------------------------------------------
# Real content pool -- never fabricated, see module docstring.
# ---------------------------------------------------------------------------

def _load_test_case_pdfs() -> list[str]:
    """Splits data/Test Cases*.pdf on their own 'CASE <n>' markers into individual real
    consultation transcripts -- 9 total across the 3 files."""
    from pypdf import PdfReader

    segments = []
    for fname in ["Test Cases.pdf", "Test Cases (1).pdf", "Test Cases (2).pdf"]:
        path = REPO_ROOT / "data" / fname
        if not path.exists():
            continue
        text = "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        parts = re.split(r"CASE\s+\d+\s", text)
        for part in parts:
            part = part.strip()
            if len(part.split()) > 150:  # drop the pre-first-marker fragment / TOC noise
                segments.append(part)
    return segments


def _load_curated_transcripts() -> list[str]:
    from tests.scenarios.curated_use_cases import OPD_USE_CASES

    return [c["transcript"] for c in OPD_USE_CASES if c.get("transcript")]


def _clean_for_tts(text: str) -> str:
    """Strips PDF-extraction artifacts pyttsx3 doesn't need to see -- content words are
    untouched. Verified live and necessary, not cosmetic: pypdf's extract_text() on
    data/Test Cases*.pdf puts almost every WORD on its own line (a property of how that PDF was
    generated, not a pypdf bug) -- collapsing only 2+ consecutive newlines (this function's
    original behavior) left every single word as its own paragraph, and the SAPI5 voice pauses
    between paragraphs the same way it would between sentences. Measured impact: a segment's
    real synthesis rate dropped to ~57 words/minute (vs. ~176 wpm for the same voice on
    normally-flowing text) -- almost entirely inter-word pause time, not the voice actually
    speaking slower. Collapsing EVERY newline (not just runs of 2+) to a single space fixes this;
    TTS pacing only needs real sentence punctuation, not source line breaks."""
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def _is_tts_safe(text: str) -> bool:
    """Only the Windows SAPI5 English voices (David/Zira) are installed on this machine -- no
    Hindi voice. Verified live: feeding that English voice real Devanagari-script text (several
    curated_use_cases.py entries are pure Hindi, not Hinglish-in-Latin-script) made
    engine.runAndWait() hang indefinitely, not just mispronounce -- confirmed by killing a stuck
    process that sat at ~0% CPU making no progress. Excluded here entirely (zero tolerance, not
    a percentage threshold) rather than relying on _tts_worker.py's subprocess timeout alone to
    catch every case -- a segment that merely stalls for minutes instead of hanging forever would
    still be caught by the timeout, but at the cost of burning that time on every occurrence
    across a multi-hundred-case run. This is a test-tooling limitation (no Hindi SAPI voice on
    this dev machine), not a finding about the app itself, which never does TTS at all."""
    return not _DEVANAGARI_RE.search(text)


_CONTENT_POOL: list[str] | None = None


def _content_pool() -> list[str]:
    global _CONTENT_POOL
    if _CONTENT_POOL is None:
        raw = [_clean_for_tts(t) for t in _load_test_case_pdfs() + _load_curated_transcripts()]
        pool = [t for t in raw if t and _is_tts_safe(t)]
        excluded = len(raw) - len(pool)
        if excluded:
            print(f"[content pool] excluded {excluded}/{len(raw)} segment(s) containing Devanagari script "
                  "(no Hindi SAPI voice on this machine -- see _is_tts_safe)")
        if not pool:
            raise RuntimeError("No real dialogue content found -- check data/Test Cases*.pdf and curated_use_cases.py")
        _CONTENT_POOL = pool
    return _CONTENT_POOL


def build_case_text(case_id: str, target_minutes: float) -> str:
    """Deterministically (seeded on case_id) concatenates real segments from the content pool
    until reaching ~target_minutes worth of words at WORDS_PER_MINUTE, cycling back through the
    pool (shuffled) if one pass isn't enough -- see module docstring for why this never invents
    new content instead."""
    target_words = int(target_minutes * WORDS_PER_MINUTE)
    rng = random.Random(case_id)
    pool = list(_content_pool())

    pieces: list[str] = []
    word_count = 0
    while word_count < target_words:
        rng.shuffle(pool)
        for segment in pool:
            pieces.append(segment)
            word_count += len(segment.split())
            if word_count >= target_words:
                break
    return "\n\n".join(pieces)


# ---------------------------------------------------------------------------
# Audio synthesis: pyttsx3 -> WAV -> ffmpeg -> WebM/Opus @ 24kbps mono (matches production)
# ---------------------------------------------------------------------------

_FFMPEG_EXE: str | None = None


def _ffmpeg() -> str:
    global _FFMPEG_EXE
    if _FFMPEG_EXE is None:
        import imageio_ffmpeg
        _FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
    return _FFMPEG_EXE


_TTS_WORKER = str(Path(__file__).resolve().parent / "_tts_worker.py")
_TTS_TIMEOUT_SEC = 180  # generous for even a 60-min-target text at this engine's real speed


def synthesize_audio(text: str, tmp_dir: str) -> tuple[bytes, float]:
    """Returns (webm_opus_bytes, actual_duration_sec). Duration is MEASURED from the real
    encoded output, never estimated from word count -- word-count-based targeting only decides
    how much text to feed in; what actually comes out is what's tested.

    Runs pyttsx3 in a child process (see _tts_worker.py's docstring for the live hang this
    guards against) with a hard timeout -- raises TimeoutError rather than ever blocking a
    multi-hundred-case run on one bad segment."""
    text_path = os.path.join(tmp_dir, "input.txt")
    wav_path = os.path.join(tmp_dir, "raw.wav")
    webm_path = os.path.join(tmp_dir, "out.webm")
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(text)

    try:
        subprocess.run(
            [sys.executable, _TTS_WORKER, text_path, wav_path],
            check=True, capture_output=True, timeout=_TTS_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"TTS synthesis exceeded {_TTS_TIMEOUT_SEC}s (text: {len(text.split())} words)") from exc

    subprocess.run(
        [_ffmpeg(), "-y", "-i", wav_path, "-ac", "1", "-c:a", "libopus", "-b:a", "24000",
         "-application", "voip", webm_path],
        check=True, capture_output=True,
    )

    with wave.open(wav_path, "rb") as w:
        # ffmpeg's own printed duration would also work, but re-deriving from the source WAV
        # (frames / rate) needs no stdout parsing and is exact.
        source_duration = w.getnframes() / float(w.getframerate())

    with open(webm_path, "rb") as f:
        webm_bytes = f.read()
    return webm_bytes, source_duration


# ---------------------------------------------------------------------------
# App bootstrap -- same safety pattern as tests/scale/runner.py, copied verbatim.
# ---------------------------------------------------------------------------

def _bootstrap_app():
    os.chdir(BACKEND_DIR)
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    db_path = Path(tempfile.mkdtemp(prefix="aivana_audio_scale_db_")) / "scale_run.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.setdefault("SECRET_KEY", "scale-run-secret-not-for-production")
    os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

    from app import main as app_main
    from app.config import settings

    assert "postgres" not in settings.DATABASE_URL.lower(), (
        "Refusing to run: DATABASE_URL resolved to a non-SQLite URL. This must never touch "
        "the production database."
    )
    if not settings.GROQ_API_KEY:
        print("WARNING: GROQ_API_KEY is empty -- Groq calls will fail. Check backend/.env.")
    if not settings.SARVAM_STT_API_KEY:
        print("WARNING: SARVAM_STT_API_KEY is empty -- Sarvam Batch STT calls will fail. Check backend/.env.")
    if settings.TRANSCRIPTION_PROVIDER != "sarvam":
        print(f"WARNING: TRANSCRIPTION_PROVIDER={settings.TRANSCRIPTION_PROVIDER!r}, not 'sarvam' -- "
              "this run will not exercise the Sarvam Batch STT path being investigated.")
    return app_main


def _make_doctor(app_main):
    from types import SimpleNamespace
    from app.models import Organization, User, PasswordHistory
    from app import auth as app_auth

    db = app_main.SessionLocal()
    try:
        org = Organization(name="AIvana Audio-Duration Scale Test Org")
        db.add(org)
        db.flush()
        pw_hash = app_auth.get_password_hash("Str0ng!Passw0rd#1")
        user = User(email="doctor@audio-scale-test.aivana", password_hash=pw_hash, role="Doctor",
                    organization_id=org.id, status="Active")
        db.add(user)
        db.flush()
        db.add(PasswordHistory(user_id=user.id, password_hash=pw_hash))
        db.commit()
        db.refresh(user)
        return SimpleNamespace(id=user.id, email=user.email, role=user.role, organization_id=user.organization_id)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Results persistence -- resumable, matching tests/scale/runner.py's convention.
# ---------------------------------------------------------------------------

def _already_done() -> set[str]:
    if not RESULTS_PATH.exists():
        return set()
    done = set()
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["case_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def _append_result(result: dict) -> None:
    with open(RESULTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")


# ---------------------------------------------------------------------------
# One case
# ---------------------------------------------------------------------------

def run_one_case(case_id: str, target_minutes: float, base_url: str, headers: dict, app_main) -> dict:
    import requests as _requests

    result = {
        "case_id": case_id, "target_minutes": target_minutes, "timestamp": time.time(),
    }
    try:
        text = build_case_text(case_id, target_minutes)
        result["source_word_count"] = len(text.split())

        with tempfile.TemporaryDirectory(prefix="audio_case_") as tmp_dir:
            t0 = time.time()
            audio_bytes, actual_duration_sec = synthesize_audio(text, tmp_dir)
            result["synthesis_sec"] = round(time.time() - t0, 2)
            result["actual_audio_duration_sec"] = round(actual_duration_sec, 1)
            result["audio_bytes"] = len(audio_bytes)

            audio_path = AUDIO_DIR / f"{case_id}.webm"
            with open(audio_path, "wb") as f:
                f.write(audio_bytes)
            result["audio_path"] = str(audio_path.relative_to(REPO_ROOT))

        # Real HTTP POST to the real endpoint -- exactly what a browser does at Stop().
        t0 = time.time()
        try:
            resp = _requests.post(
                f"{base_url}/api/transcribe-audio",
                files={"audio": (f"{case_id}.webm", audio_bytes, "audio/webm")},
                headers=headers, timeout=1900,
            )
            result["transcribe_latency_sec"] = round(time.time() - t0, 2)
            result["transcribe_status_code"] = resp.status_code
            if resp.status_code == 200:
                transcript = resp.json().get("transcript", "")
                result["transcribe_success"] = True
                result["transcript_word_count"] = len(transcript.split())
                transcript_path = TRANSCRIPT_DIR / f"{case_id}.txt"
                with open(transcript_path, "w", encoding="utf-8") as f:
                    f.write(transcript)
                result["transcript_path"] = str(transcript_path.relative_to(REPO_ROOT))
            else:
                result["transcribe_success"] = False
                result["transcribe_error"] = resp.text[:500]
                return result
        except _requests.exceptions.RequestException as exc:
            result["transcribe_latency_sec"] = round(time.time() - t0, 2)
            result["transcribe_success"] = False
            result["transcribe_error"] = f"{type(exc).__name__}: {exc}"
            return result

        # Real downstream scribe call (real Groq) -- the SAME pipeline a doctor's Stop click
        # actually drives, not just the STT leg in isolation.
        t0 = time.time()
        try:
            draft = app_main.scribe.scribe_transcript(transcript)
            result["scribe_latency_sec"] = round(time.time() - t0, 2)
            result["scribe_success"] = True
            result["scribe_transcript_chunked"] = draft.get("transcriptChunked", False)
            result["scribe_medications_count"] = len(draft.get("medications") or [])
            result["scribe_chief_complaint_present"] = bool((draft.get("chiefComplaint") or "").strip())
            # The actual signal for "did the underlying Groq call(s) genuinely fail" -- was
            # missing from this harness entirely (a gap in the test, not the app) until now,
            # which is why earlier runs could only infer failure indirectly from meds=0/
            # cc_present=False instead of reading it directly.
            result["scribe_note_extraction_failed"] = draft.get("noteExtractionFailed", None)
        except Exception as exc:
            result["scribe_latency_sec"] = round(time.time() - t0, 2)
            result["scribe_success"] = False
            result["scribe_error"] = f"{type(exc).__name__}: {exc}"

        return result
    except Exception as exc:
        result["harness_error"] = f"{type(exc).__name__}: {exc}"
        return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _weighted_duration_sequence(n: int, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    return rng.choices(DURATION_BUCKETS_MINUTES, weights=DURATION_WEIGHTS, k=n)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", type=int, default=None,
                         help="Run one real case per duration bucket (%d cases) for calibration" % len(DURATION_BUCKETS_MINUTES))
    parser.add_argument("--total-cases", type=int, default=None)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    if not args.no_resume:
        done = _already_done()
        print(f"Resuming: {len(done)} case(s) already in {RESULTS_PATH}")
    else:
        done = set()

    if args.smoke is not None:
        durations = list(DURATION_BUCKETS_MINUTES)
        case_ids = [f"SMOKE-{d:02d}min" for d in durations]
    elif args.total_cases:
        durations = _weighted_duration_sequence(args.total_cases)
        case_ids = [f"AUD-{i:05d}-{int(d)}min" for i, d in enumerate(durations)]
    else:
        parser.error("Pass --smoke N or --total-cases N")
        return

    pending = [(cid, d) for cid, d in zip(case_ids, durations) if cid not in done]
    print(f"{len(pending)} case(s) to run (of {len(case_ids)} total)")
    if not pending:
        return

    app_main = _bootstrap_app()
    from tests._voice_helpers import start_live_server, mint_tokens

    doctor = _make_doctor(app_main)

    base_url, stop = start_live_server(app_main.app)
    print(f"Live server: {base_url}")
    try:
        for i, (case_id, target_minutes) in enumerate(pending, 1):
            print(f"[{i}/{len(pending)}] {case_id} (target {target_minutes} min) ...", flush=True)
            # Minted fresh per case, not once for the whole batch -- ACCESS_TOKEN_EXPIRE_MINUTES
            # is 15 (config.py). Verified live: a batch that ran long enough to cross that
            # boundary got "Invalid token" 401s on later cases, which looked like (but was not)
            # a real STT/duration failure -- a test-harness bug, not a finding about the app.
            tokens = mint_tokens(doctor)
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            t0 = time.time()
            result = run_one_case(case_id, target_minutes, base_url, headers, app_main)
            elapsed = time.time() - t0
            result["total_wall_sec"] = round(elapsed, 2)
            _append_result(result)
            # "scribe_success" only means scribe_transcript() didn't raise -- it never raises by
            # design, even when the underlying Groq call genuinely failed (see scribe.py's
            # noteExtractionFailed). Read that flag explicitly so the console output doesn't
            # call a silently-empty draft "OK".
            if not result.get("transcribe_success"):
                status = "STT-FAIL"
            elif not result.get("scribe_success"):
                status = "SCRIBE-FAIL"
            elif result.get("scribe_note_extraction_failed"):
                status = "EXTRACTION-FAILED (blank draft)"
            else:
                status = "OK"
            print(f"    -> {status} in {elapsed:.1f}s "
                  f"(audio={result.get('actual_audio_duration_sec', '?')}s, "
                  f"transcribe={result.get('transcribe_latency_sec', '?')}s)")
    finally:
        stop()

    print(f"Results appended to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
