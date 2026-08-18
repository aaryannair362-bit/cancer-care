import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./aivana.db"
    SECRET_KEY: str = "your-super-secret-key-change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    # Higher-limit developer-tier key, kept as a separate .env entry rather than overwriting
    # GROQ_API_KEY directly so the original key stays available/documented as a fallback.
    GROQ_API_KEY_Prod: str = os.getenv("GROQ_API_KEY_Prod", "")
    # Env-overridable (not hardcoded) so a model swap is a Render dashboard env var edit, not a
    # code deploy -- learned the hard way: GROQ_AUDIO_MODEL wasn't overridable this way either
    # when it was changed in-code, which turned one wrong model guess into a broken-production-
    # and-redeploy cycle instead of a five-second toggle back. Default here matches what's
    # actually set in Render's env (confirmed directly, not guessed): production moved off
    # qwen/qwen3.6-27b to llama-3.1-8b-instant because qwen couldn't keep up with concurrent
    # call volume from OPD+IPD -- but until GROQ_MODEL became env-overridable, that env var was
    # silently ignored by this hardcoded default, so any redeploy of an environment missing the
    # Render env var (a fresh env, CI, etc.) would have silently regressed back to the
    # already-known-too-slow qwen model with no warning. Matching the default to the real,
    # working value removes that trap regardless of the env var.
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    # Whisper model used for POST /api/transcribe-audio (see scribe.py's transcribe_audio) --
    # a separate setting from GROQ_MODEL since it's a different model for a different Groq
    # endpoint. MUST be "whisper-large-v3", not "-turbo" or "distil-whisper-large-v3-en":
    # transcribe_audio calls Groq's /audio/TRANSLATIONS endpoint specifically (not
    # /audio/transcriptions), and Groq's hosted translations task only supports the full
    # whisper-large-v3 model -- the turbo/distilled variants are transcription-only there.
    # Tried "-turbo" here for a real, wanted latency win (see git history) and it broke
    # transcription in production ("Transcription failed" on every recording, confirmed live)
    # -- reverted same-day. If a faster audio path is wanted later, it has to come from
    # switching endpoints (transcriptions + explicit language handling) or a different
    # provider/local model, not from swapping the model on this same endpoint.
    GROQ_AUDIO_MODEL: str = os.getenv("GROQ_AUDIO_MODEL", "whisper-large-v3")
    # Sarvam AI's Saaras v3 (sarvam_transcriber.py) -- an alternative audio-transcription
    # provider, purpose-built and benchmarked for Hindi/English code-switched speech
    # specifically. Verified live before this became the default: real API calls against the
    # actual key confirmed the request/response contract, confirmed a <=30s chunk round-trips
    # in ~1.4s, and confirmed the documented 30-second-per-request cap with a real 400
    # response -- but NOT yet verified with a real recording through a real browser (only the
    # API mechanics and the mocked unit/integration tests) -- see sarvam_transcriber.py's
    # module docstring. Chosen as the default by explicit product decision despite that gap;
    # if real-world results look wrong, this is a one-line Render env var revert to "whisper"
    # (today's previously-default, still fully working, unchanged path), not a code deploy --
    # same reasoning as GROQ_MODEL/GROQ_AUDIO_MODEL above.
    SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY", "")
    TRANSCRIPTION_PROVIDER: str = os.getenv("TRANSCRIPTION_PROVIDER", "sarvam")
    # Comma-separated list of origins allowed to call the API. The frontend is same-origin
    # (served by this same FastAPI process, API_BASE = '/api'), so this only matters for local
    # dev on a different port/live-server and any future separately-hosted frontend.
    ALLOWED_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:8000"
    # Disabled in tests (tests/conftest.py, tests/scale/runner.py) -- many legitimate tests
    # fire dozens of auth calls from the same test-client "IP" in well under a minute.
    RATE_LIMIT_ENABLED: bool = True

    class Config:
        env_file = (
            os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
            ".env"
        )

settings = Settings()
# Prefer the developer-tier key (higher rate limits) whenever it's configured; every caller
# in the app reads settings.GROQ_API_KEY, so this is the one place that needs to know both
# exist.
if settings.GROQ_API_KEY_Prod:
    settings.GROQ_API_KEY = settings.GROQ_API_KEY_Prod

_INSECURE_DEFAULT_SECRET_KEY = "your-super-secret-key-change-this-in-production"
if settings.SECRET_KEY == _INSECURE_DEFAULT_SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is still the well-known FastAPI tutorial default. Anyone who knows this "
        "value can forge a valid JWT for any user/role. Set a real random SECRET_KEY in "
        "backend/.env (e.g. `python -c \"import secrets; print(secrets.token_hex(32))\"`) or "
        "the SECRET_KEY environment variable before starting the app."
    )