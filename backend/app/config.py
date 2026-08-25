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
    # Sarvam AI's Saaras v3 (sarvam_transcriber.py) -- audio transcription engine
    # purpose-built for Hindi/English medical speech and code-switching.
    SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY", "")
    TRANSCRIPTION_PROVIDER: str = os.getenv("TRANSCRIPTION_PROVIDER", "sarvam")
    # Allowed CORS origins. Since frontend is served by FastAPI directly, requests are
    # same-origin by default. "*" or specific domains allow external access.
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "*")
    RATE_LIMIT_ENABLED: bool = True
    MAX_PATIENT_DOCUMENT_MB: int = 25
    # EasyOCR language codes (comma-separated, e.g. "en,hi"), NOT Tesseract's "eng"-style codes
    # -- see ocr_service.py's module docstring for why OCR moved off Tesseract, and
    # https://www.jaided.ai/easyocr for the supported-language list.
    OCR_LANGUAGES: str = os.getenv("OCR_LANGUAGES", "en")
    # Read by main.py's create_default_user() to auto-seed the first Admin account on a fresh
    # deploy (empty DB). Declared here so settings.ADMIN_EMAIL doesn't raise AttributeError --
    # it previously did on every single startup (caught by that function's broad except, so the
    # app kept running, but the auto-seed silently never ran even when these were set in the
    # environment). Blank by default: leaving them unset skips the seed and requires registering
    # the first Admin manually via POST /api/auth/register instead.
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")
    ADMIN_ORG_NAME: str = os.getenv("ADMIN_ORG_NAME", "Default Organization")

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
