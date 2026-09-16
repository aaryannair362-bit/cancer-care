import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./aivana.db"
    SECRET_KEY: str = "your-super-secret-key-change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
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
    # Dedicated key for PDF/document OCR AI-extraction (cca_engine.py's extract_clinical_facts /
    # classify_and_extract_page) -- kept on its OWN Groq account/key so a multi-page document
    # upload's token usage never competes with GROQ_API_KEY's 8000-token/minute budget for live
    # OPD/IPD consultation scribing (see rate_limiter.py's ocr_extraction_* buckets). Falls back
    # to GROQ_API_KEY below (post-construction) when not set, so a deployment that hasn't been
    # given a dedicated key yet keeps working exactly as before -- both workloads sharing one
    # budget, not a broken one.
    GROQ_API_KEY_OCR: str = os.getenv("GROQ_API_KEY_OCR", "")
    # Sarvam AI's Saaras v3 (sarvam_batch_transcriber.py) -- audio transcription engine
    # purpose-built for Hindi/English medical speech and code-switching. Legacy shared
    # credential, kept only as the fallback SARVAM_OCR_API_KEY/SARVAM_STT_API_KEY use below when
    # a deployment hasn't been given their own dedicated keys.
    SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY", "")
    # Split, feature-specific Sarvam credentials: Document AI (OCR) and Batch Speech-to-Text
    # (Scribe's audio transcription) are billed and rate-limited independently on Sarvam's side
    # per key, so a burst on one feature can no longer exhaust the other's quota by sharing one
    # account. Each falls back to the shared SARVAM_API_KEY above (resolved post-construction
    # below, same reason GROQ_API_KEY_OCR is) when its own dedicated key isn't set, so a
    # deployment that hasn't been given split keys yet keeps working unchanged.
    SARVAM_OCR_API_KEY: str = os.getenv("SARVAM_OCR_API_KEY", "")
    SARVAM_STT_API_KEY: str = os.getenv("SARVAM_STT_API_KEY", "")
    TRANSCRIPTION_PROVIDER: str = os.getenv("TRANSCRIPTION_PROVIDER", "sarvam")
    # The finalized, tested model as of this writing (verified live against the real Batch
    # Speech-to-Text API) -- see sarvam_batch_transcriber.py. Not every model Sarvam offers
    # works for this app's use case: mode="translate" (always-English output for Hinglish
    # speech) only takes effect on saaras:v3/v4, not the SDK's own default ("saarika:v2.5").
    SARVAM_STT_MODEL: str = os.getenv("SARVAM_STT_MODEL", "saaras:v3")
    # BCP-47 language code passed to Sarvam Document AI (see ocr_service.py). Finalized default
    # is English -- but see CHANGELOG/session notes: a real production document containing
    # Bengali script characters surfaced this as a real, not just theoretical, tuning knob.
    # Left here (not hardcoded) so a deployment serving predominantly non-English documents can
    # change it without a code deploy.
    SARVAM_OCR_LANGUAGE: str = os.getenv("SARVAM_OCR_LANGUAGE", "en-IN")
    # "sarvam" (Sarvam Document AI, see ocr_service.py) or "local" (RapidOCR, no external call).
    # Left blank by default here -- resolved below, after Settings() construction, against the
    # already-loaded SARVAM_API_KEY (same reason GROQ_API_KEY_OCR is applied post-construction
    # below: a bare os.getenv() at class-body-eval time can't see a key that only exists in the
    # backend/.env file, which pydantic-settings loads later, during Settings() itself).
    OCR_PROVIDER: str = os.getenv("OCR_PROVIDER", "")
    # Allowed CORS origins. Since frontend is served by FastAPI directly, requests are
    # same-origin by default. "*" or specific domains allow external access.
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "*")
    RATE_LIMIT_ENABLED: bool = True
    # A real multi-page scanned hospital record (every page a full-resolution photo, no
    # compression applied by the scanning hospital) legitimately exceeds a low cap -- confirmed
    # against real files in data_insurance/: a 30-page fully-scanned case file measured 36.4MB.
    # Matched exactly to Sarvam Document AI's own accepted upload size (200MB, docs.sarvam.ai)
    # rather than picking an arbitrary number above it -- raising this doesn't shift the failure
    # to the OCR vendor, since nothing this app could ever accept would exceed what Sarvam itself
    # already refuses. Deliberate trade-off, not an oversight: this Render instance runs with a
    # 512MB RAM ceiling (see ocr_service.py's module docstring for the docTR-vs-RapidOCR memory
    # incident that ceiling already caused once) -- a 200MB upload read fully into memory
    # (routers/cca.py's upload_document) leaves materially less headroom than the old 60MB cap
    # did. Accepted here because Sarvam's own limit is the more meaningful ceiling for what a
    # legitimate hospital record ever looks like; if a real 100MB+ upload ever OOMs this process,
    # that's the signal to move upload_document to a streamed/chunked read instead of lowering
    # this back down.
    MAX_PATIENT_DOCUMENT_MB: int = 200
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
# GROQ_API_KEY_OCR falls back to GROQ_API_KEY when not set, so a deployment that hasn't been
# given a dedicated OCR key yet keeps working exactly as before (both workloads sharing one
# budget, not a broken one).
if not settings.GROQ_API_KEY_OCR:
    settings.GROQ_API_KEY_OCR = settings.GROQ_API_KEY

# Each dedicated Sarvam key falls back to the shared SARVAM_API_KEY when not explicitly set --
# see the SARVAM_OCR_API_KEY/SARVAM_STT_API_KEY declarations above for why they're split.
if not settings.SARVAM_OCR_API_KEY:
    settings.SARVAM_OCR_API_KEY = settings.SARVAM_API_KEY
if not settings.SARVAM_STT_API_KEY:
    settings.SARVAM_STT_API_KEY = settings.SARVAM_API_KEY

# No explicit OCR_PROVIDER override: default to Sarvam Document AI only when a Sarvam OCR key is
# actually configured, so a deployment/test run with no Sarvam key keeps working exactly as it
# always did (local RapidOCR only) rather than silently trying an external call with no
# credential for it.
if not settings.OCR_PROVIDER:
    settings.OCR_PROVIDER = "sarvam" if settings.SARVAM_OCR_API_KEY else "local"

_INSECURE_DEFAULT_SECRET_KEY = "your-super-secret-key-change-this-in-production"
if settings.SECRET_KEY == _INSECURE_DEFAULT_SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is still the well-known FastAPI tutorial default. Anyone who knows this "
        "value can forge a valid JWT for any user/role. Set a real random SECRET_KEY in "
        "backend/.env (e.g. `python -c \"import secrets; print(secrets.token_hex(32))\"`) or "
        "the SECRET_KEY environment variable before starting the app."
    )
