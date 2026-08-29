import json
import logging
import re
import time
import requests
from .config import settings
from . import drug_matcher
from . import lab_test_matcher
from . import rate_limiter

MAX_RATE_LIMIT_RETRIES = 3

# Doctor-patient transcripts and the AI's structured output derived from them are PHI. Raw
# request/response content is only ever emitted at DEBUG (off by default -- Python's logging
# defaults to WARNING when unconfigured), so a log aggregator capturing default-level output
# never sees it; only someone who deliberately enables DEBUG logging does.
logger = logging.getLogger(__name__)

class ScribeEngine:
    def __init__(self):
        self.api_key = settings.GROQ_API_KEY
        self.model = settings.GROQ_MODEL
        self.audio_model = getattr(settings, "GROQ_AUDIO_MODEL", "whisper-large-v3")
        self.base_url = "https://api.groq.com/openai/v1/chat/completions"
        self._reasoning_format_supported = True
        logger.info("GROQ_API_KEY present: %s, length: %d, model: %s",
                    bool(self.api_key), len(self.api_key) if self.api_key else 0, self.model)

        self.system_prompt = """You are an exceptionally precise clinical transcription assistant (scribe) for a General Medicine OPD clinician.
Analyze the doctor-patient conversation transcript and synthesize an accurate clinical prescription draft with maximum fidelity to the spoken facts.

Your absolute highest priority directive is to STRICTLY report the conversation:
1. PURELY report spoken facts. Do NOT add, invent, or assume any facts, clinical developments, or medications that were not mentioned.
2. If any element has no mention in the transcript, return a blank string "" or an empty array [].
3. STRICTLY DISTINGUISH BETWEEN:
   - "chiefComplaint": Subjective symptoms reported by the patient
   - "primaryDiagnosis": The formal clinical assessment or clinical diagnosis made by the clinician
4. SYMPTOM ACCURACY AND NEGATION PREVENTION:
   - Listen carefully to positive reports of symptoms
   - Do NOT hallucinate false-negatives unless the patient explicitly denies that symptom
5. Handle spoken names, medicines, or measurements gracefully
6. HPI VS PHYSICAL EXAMINATION -- KEEP THESE SEPARATE: "hpi" is the patient's history as reported -- symptoms, duration/progression of illness, aggravating/relieving factors, vitals the patient reports or that are read out as history. "physicalExam" is EXCLUSIVELY the clinician's own objective examination findings from actually examining the patient right now (e.g. "throat is red and inflamed", "chest clear on auscultation", "no palpable lymphadenopathy", "abdomen soft, non-tender"). If the doctor states an examination finding, it belongs in "physicalExam", NEVER duplicated into or substituted for "hpi". If no examination finding was spoken, "physicalExam" is "".
7. NEVER include medication names, doses, frequencies, or other treatment/prescription details anywhere in "chiefComplaint" or "hpi" -- those belong EXCLUSIVELY in the "medications" array, even if the doctor mentions them in the same breath as a symptom (e.g. "for the fever I gave paracetamol" -> "fever" goes in hpi, "paracetamol" goes in medications, never both).
8. "medications" is EXCLUSIVELY for a specific, named, purchasable drug/product (a real medicine name -- "Paracetamol", "Betadine Gargle", "Crocin"), never a generic home-care or lifestyle instruction with no product name attached. "Gargle with warm salt water", "drink plenty of fluids", "rest for 2 days", "apply an ice pack", "steam inhalation" are ADVICE, not medications, even though the doctor phrases them as an instruction to do something ("gargle thrice a day with salt water" -> advice; "gargle thrice a day with Betadine" -> medications, because Betadine is a named product). If it has no product name, it is never a medication."""

    def _post_with_retry(self, url: str, request_kwargs: dict, _retry: int = 0) -> dict:
        """
        Shared POST-with-429-backoff-retry and PHI-safe error logging for any Groq REST
        call (chat completions or audio translations) -- owns only the retry/error envelope,
        not the payload shape, so `request_kwargs` (headers/json/files/data/timeout/...) is
        passed straight through to `requests.post` unexamined. Returns the parsed JSON body.
        """
        try:
            response = requests.post(url, **request_kwargs)
            if response.status_code == 429 and _retry < MAX_RATE_LIMIT_RETRIES:
                # A 429 here used to fall straight through to _generate_json's fallback --
                # silently degrading a real consultation to an empty draft on nothing more
                # than a transient rate-limit blip. Retry_after gives transient limiting a
                # real chance to clear before giving up -- but MUST be capped: verified live,
                # under sustained quota pressure Groq's Retry-After can be minutes (even
                # 25+ minutes), and honoring that literally would hang a single doctor's
                # consultation request for that long. Capping means we sometimes retry before
                # the server says we're truly clear (and get 429'd again, consuming another
                # attempt), which is the right tradeoff -- a slightly wasted retry beats
                # blocking a real request for tens of minutes.
                retry_after = response.headers.get("retry-after")
                wait = min(float(retry_after), 20) if retry_after else min(3 * (2 ** _retry), 20)
                logger.warning("Groq 429 rate limited, retrying in %.1fs (attempt %d/%d)",
                               wait, _retry + 1, MAX_RATE_LIMIT_RETRIES)
                time.sleep(wait)
                return self._post_with_retry(url, request_kwargs, _retry=_retry + 1)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error("Groq API error: %s", e)
            if hasattr(e, 'response') and e.response is not None:
                # response body can echo request content back (PHI) -- debug-only
                logger.debug("Response body: %s", e.response.text)
            raise

    def _call_groq_api(self, prompt: str, system: str = None, temperature: float = 0.3) -> str:
        if not self.api_key:
            raise ValueError("Groq API key not configured. Set GROQ_API_KEY in environment.")

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system or self.system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": temperature,
            "max_tokens": 3000,
        }
        if self._reasoning_format_supported:
            # Reasoning-capable models (e.g. the qwen3 line) emit a <think>...</think>
            # chain-of-thought block INSIDE `content` before the actual answer by default,
            # consuming the token budget on reasoning alone -- verified live, a call with
            # this omitted on such a model hit max_tokens mid-<think> and returned ZERO
            # actual answer content, every time. "hidden" moves the reasoning out of
            # `content` entirely. Not every model supports this param though (verified live:
            # a non-reasoning model 400s with `reasoning_format is not supported with this
            # model`) -- self._reasoning_format_supported starts True and flips to False
            # below the first time that happens, so we stop asking for it on this model for
            # the rest of the process instead of eating a failed request every single call.
            payload["reasoning_format"] = "hidden"

        # Proactive pacing BEFORE dispatch, not just reactive retry-after-429 (see
        # rate_limiter.py's module docstring for the live incident that motivated this --
        # concurrent OPD+IPD callers could 429 together and each independently sit in the
        # retry loop below at the same time). Blocks this thread until it's this call's turn;
        # under normal, non-concurrent usage the buckets are full and this returns immediately.
        rate_limiter.request_bucket.consume(1)
        rate_limiter.token_bucket.consume(rate_limiter.estimate_tokens(prompt, payload["max_tokens"]))

        try:
            data = self._post_with_retry(self.base_url, {"headers": headers, "json": payload, "timeout": 60})
        except requests.exceptions.RequestException as e:
            response = getattr(e, "response", None)
            if (
                self._reasoning_format_supported and response is not None
                and response.status_code == 400 and "reasoning_format" in response.text
            ):
                self._reasoning_format_supported = False
                return self._call_groq_api(prompt, system, temperature)
            raise
        return data["choices"][0]["message"]["content"]

    def transcribe_audio(self, audio_bytes: bytes, content_type: str, filename: str) -> str:
        """
        Translates spoken audio to English text via Groq's Whisper (/audio/translations,
        not /audio/transcriptions) -- deliberately always-English output rather than
        transcription-in-original-language, because doctors/nurses here speak code-switched
        Hindi+English (Hinglish): a language="hi" transcription would force the WHOLE output
        into Devanagari script, garbling the embedded English drug names and dosage
        abbreviations (mg/ml/TDS/BD/OD) the rest of this pipeline assumes are English.
        Translation mode transcribes English portions as-is and translates Hindi portions to
        English, which is what the downstream scribe_transcript()/drug_matcher/
        lab_test_matcher pipeline needs. Never logs audio_bytes or the transcribed text
        (see module docstring's PHI note) -- errors propagate to the caller un-logged-in-detail
        by _post_with_retry, same as _call_groq_api.
        """
        if not self.api_key:
            raise ValueError("Groq API key not configured. Set GROQ_API_KEY in environment.")

        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": (filename, audio_bytes, content_type)}
        data = {
            "model": self.audio_model,
            "response_format": "json",
            # Biases Whisper's vocabulary toward the domain it'll actually see, without
            # constraining language -- improves recognition of drug names/dosage shorthand
            # that a generic model would otherwise be prone to mis-hearing.
            "prompt": (
                "Indian outpatient clinical consultation, mixed Hindi and English (Hinglish) "
                "speech. Contains medicine names, dosages, and abbreviations such as mg, ml, "
                "TDS, BD, OD, HS."
            ),
        }
        result = self._post_with_retry(
            "https://api.groq.com/openai/v1/audio/translations",
            {"headers": headers, "files": files, "data": data, "timeout": 90},
        )
        # Groq's /audio/translations response shape is {"text": str} -- NOT {"transcript":...}
        # like this app's own /api/scribe response, and totally unrelated to the
        # chat-completions {"choices":[{"message":{"content":...}}]} shape _call_groq_api
        # parses. Easy to get wrong by habit; there is no shared precedent in this file.
        return (result.get("text") or "").strip()

    def _generate_json(self, prompt: str, system: str = None, temperature: float = 0.3) -> dict:
        try:
            raw = self._call_groq_api(prompt, system, temperature)
            logger.debug("RAW RESPONSE: %s...", raw[:500])
            # Defense-in-depth: _call_groq_api already requests reasoning_format="hidden" so
            # a <think>...</think> block should never appear in `content`, but strip one out
            # if it does anyway (e.g. a future model/provider change reintroduces it) rather
            # than let it corrupt every downstream json.loads.
            cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            # Try parsing as JSON
            result = json.loads(cleaned)
            if not isinstance(result, dict):
                # This function is documented (and every caller assumes) to return a dict.
                # Valid JSON that happens to parse to a list/string/number ("[1, 2, 3]", "42",
                # "true") is not a parse error, so it wouldn't hit the except clause below --
                # but every caller immediately does result.get(...), which would crash with a
                # raw AttributeError. Treat a non-dict result the same as a parse failure.
                logger.warning("Groq returned valid JSON of the wrong shape (%s, expected dict)",
                               type(result).__name__)
                logger.debug("Wrong-shape content: %s", cleaned[:200])
                return self._fallback_extract(raw)
            return result
        except json.JSONDecodeError as e:
            logger.error("JSON parsing error: %s", e)
            logger.debug("Raw content that failed: %s", raw[:1000])
            # Some models wrap the JSON in explanatory prose despite the prompt asking for
            # pure JSON (verified live: "Based on the transcript, here's..." before the
            # object, "Note that the hpi field is empty because..." after it) -- the fence
            # stripping above only handles a response that's ENTIRELY the JSON (optionally
            # fenced), not JSON embedded inside other text. Try the outermost {...} substring
            # before giving up to the much cruder regex fallback.
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    result = json.loads(match.group(0))
                    if isinstance(result, dict):
                        return result
                except json.JSONDecodeError:
                    pass
            return self._fallback_extract(raw)
        except Exception as e:
            logger.error("Unexpected error in _generate_json: %s", e)
            return {}

    def _fallback_extract(self, text: str) -> dict:
        """Try to extract structured data from raw text using regex if JSON fails."""
        result = {
            "chiefComplaint": "",
            "hpi": "",
            "physicalExam": "",
            "primaryDiagnosis": "",
            "differentialDiagnosis": "",
            "medications": [],
            "advice": "",
            "labTests": []
        }
        # Try to find sections by common headings
        lines = text.split('\n')
        current_section = None
        buffer = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Detect section headers
            lower = line.lower()
            if "chief complaint" in lower or "chiefcomplaint" in lower:
                current_section = "chiefComplaint"
                continue
            elif "history of present illness" in lower or "hpi" in lower:
                current_section = "hpi"
                continue
            elif "physical exam" in lower or "physicalexam" in lower:
                current_section = "physicalExam"
                continue
            elif "primary diagnosis" in lower or "primarydiagnosis" in lower:
                current_section = "primaryDiagnosis"
                continue
            elif "differential diagnosis" in lower or "differentialdiagnosis" in lower:
                current_section = "differentialDiagnosis"
                continue
            elif "medications" in lower:
                current_section = "medications"
                continue
            elif "advice" in lower or "instructions" in lower:
                current_section = "advice"
                continue
            elif "lab tests" in lower or "labtests" in lower:
                current_section = "labTests"
                continue
            # If we have a current section and this line looks like content (not a section header), append
            if current_section and not any(kw in lower for kw in ["chief complaint", "history", "physical exam", "primary diagnosis", "differential", "medications", "advice", "lab tests"]):
                buffer.append(line)
        # Join buffer to a single string for each section
        if buffer:
            # crude: split by double newline
            full_text = "\n".join(buffer)
            # Use simple heuristics: if we have ":" then split
            if ":" in full_text:
                parts = full_text.split(":")
                if len(parts) > 1:
                    result["chiefComplaint"] = parts[1].strip() if "chief" in parts[0].lower() else ""
            # Try to extract medications using regex
            med_pattern = r'([A-Za-z]+)\s*(\d+\.?\d*\s*(mg|g|ml|tablet|injection))'
            matches = re.findall(med_pattern, full_text, re.IGNORECASE)
            if matches:
                result["medications"] = [{"drugName": m[0], "dose": m[1], "frequency": "N/A", "route": "Oral", "duration": "N/A"} for m in matches[:5]]
        return result

    @staticmethod
    def _coerce_string_fields(result: dict, fields) -> dict:
        """
        The model doesn't always respect "return this as a string" -- verified live: a real
        call returned differentialDiagnosis as a JSON array instead of a comma-separated
        string, which crashed the Consultation insert (differential_diagnosis is a Text
        column; sqlite3/SQLAlchemy can't bind a Python list to it). Every field the prompt
        asks for as a string gets normalized here before it reaches any caller, rather than
        trusting the model's output shape at each call site.
        """
        for field in fields:
            value = result.get(field)
            if isinstance(value, list):
                result[field] = ", ".join(str(v) for v in value)
            elif value is not None and not isinstance(value, str):
                result[field] = str(value)
        return result

    def scribe_transcript(self, transcript: str) -> dict:
        prompt = f"""Process the following spoken consultation transcript and structure it perfectly.

Transcript of conversation:
"{transcript}"

Return a JSON object with the following structure:
{{
    "chiefComplaint": "Extracted patient complaints -- symptoms only, never medication names/doses",
    "hpi": "History of present illness -- the patient's reported symptoms, duration, and progression only. NEVER mention medication names, doses, or treatments here -- those go exclusively in the medications array below. NEVER include the clinician's own examination findings here -- those go exclusively in physicalExam below",
    "physicalExam": "The clinician's own objective examination findings from examining the patient (e.g. throat appearance, chest auscultation, lymph nodes, abdomen). Blank if none were spoken",
    "primaryDiagnosis": "Primary provisional clinical diagnosis",
    "differentialDiagnosis": "comma separated differential diagnoses",
    "medications": [
        {{"drugName": "", "dose": "", "frequency": "", "route": "", "duration": ""}}
    ],
    "advice": "Clinical advice, warnings and instructions -- INCLUDING generic home-care instructions with no named product (gargling with salt water, hydration, rest, ice/warm compress, steam inhalation, follow-up timing). These never belong in medications -- see system prompt rule 8",
    "labTests": ["list of recommended tests"]
}}"""
        result = self._generate_json(prompt, temperature=0.3)
        default = {
            "chiefComplaint": "", "hpi": "", "physicalExam": "", "primaryDiagnosis": "",
            "differentialDiagnosis": "", "medications": [], "advice": "", "labTests": []
        }
        for key in default:
            if key not in result or result[key] is None:
                result[key] = default[key]
        result = self._coerce_string_fields(
            result, ("chiefComplaint", "hpi", "physicalExam", "primaryDiagnosis", "differentialDiagnosis", "advice")
        )
        # Corrects each medication's drugName against the canonical medicines dataset before
        # the draft ever reaches the doctor -- see drug_matcher.py for why (ASR/LLM-introduced
        # brand-name misspellings, verified to hit a meaningful fraction of real prescriptions).
        result["medications"] = drug_matcher.correct_medication_names(result["medications"])
        # Same idea for recommended lab tests: "CBC"/"Widal"/a misspelled test name gets
        # normalized against the canonical lab test master (see lab_test_matcher.py).
        result["labTests"] = lab_test_matcher.correct_lab_test_names(result["labTests"])
        return result

    def clinical_helper(self, current_draft: dict, query: str) -> str:
        prompt = f"""You are an expert physician companion advising on this prescription.

Current Prescription Draft State:
{json.dumps(current_draft, indent=2)}

Doctor asks: "{query or 'Optimize this prescription draft, check for drug interactions, check for missing values, and suggest improvements.'}"

Provide clinical, expert-level feedback. Suggest changes or additions directly. Your tone must be supportive, professional, and clinical. Keep it concise."""
        return self._call_groq_api(prompt, temperature=0.7)

    def translate_prescription(self, draft: dict, target_language: str) -> dict:
        if target_language == "English":
            return draft
        prompt = f"""Translate this medical prescription from English into "{target_language}".

Prescription:
{json.dumps(draft, indent=2)}

Keep drug names in English. Translate descriptions, instructions, and test names. Return pure JSON."""
        result = self._generate_json(prompt, temperature=0.3)
        default = {
            "chiefComplaint": "", "hpi": "", "physicalExam": "", "primaryDiagnosis": "",
            "differentialDiagnosis": "", "medications": [], "advice": "", "labTests": []
        }
        for key in default:
            if key not in result:
                result[key] = default[key]
        return self._coerce_string_fields(
            result, ("chiefComplaint", "hpi", "physicalExam", "primaryDiagnosis", "differentialDiagnosis", "advice")
        )

    def generate_discharge_summary(self, context: dict) -> dict:
        """
        context: {
            "patient_name", "age", "gender", "ward", "admission_date", "diagnosis",
            "vitals": [{"recorded_at", "bp_systolic", "bp_diastolic", "heart_rate",
                        "temperature", "oxygen_sat", "respiratory_rate", "notes"}, ...],
            "nursing_notes": [{"created_at", "notes"}, ...],
            "tasks": [{"description", "status"}, ...],
            "consultations": [{"chief_complaint", "primary_diagnosis", "medications", "advice"}, ...],
        }
        Summarizes an inpatient's full stay into a discharge document. Mirrors
        scribe_transcript's default-backfill contract: never raises, always returns every key.
        """
        prompt = f"""You are a clinician preparing a discharge summary for an inpatient. Using ONLY the
information provided below (do not invent facts not present here), write a structured discharge summary.

Patient: {context.get('patient_name', '')}, {context.get('age', 'unknown age')}, {context.get('gender', '')}
Ward: {context.get('ward', '')}
Admission date: {context.get('admission_date', '')}
Admission diagnosis: {context.get('diagnosis', '')}

Vitals recorded during stay (chronological):
{json.dumps(context.get('vitals', []), indent=2)}

Nursing notes (chronological):
{json.dumps(context.get('nursing_notes', []), indent=2)}

Tasks/interventions:
{json.dumps(context.get('tasks', []), indent=2)}

Doctor consultations linked to this patient:
{json.dumps(context.get('consultations', []), indent=2)}

Return a JSON object with exactly these keys:
{{
    "admissionSummary": "Brief summary of why the patient was admitted and their condition on arrival",
    "hospitalCourse": "Narrative of how the patient's condition evolved during the stay, based on the vitals/notes/tasks above",
    "dischargeDiagnosis": "Final diagnosis at discharge",
    "medicationsAtDischarge": [{{"drugName": "", "dose": "", "frequency": "", "duration": ""}}],
    "followUpInstructions": "Advice, warnings, and follow-up plan for the patient",
    "conditionAtDischarge": "One-line clinical status at discharge (e.g. stable, improved, guarded)"
}}
If information for a field is not present in the input above, use an empty string or empty array -- do not fabricate."""
        result = self._generate_json(prompt, temperature=0.3)
        default = {
            "admissionSummary": "", "hospitalCourse": "", "dischargeDiagnosis": "",
            "medicationsAtDischarge": [], "followUpInstructions": "", "conditionAtDischarge": ""
        }
        for key in default:
            if key not in result or result[key] is None:
                result[key] = default[key]
        result = self._coerce_string_fields(
            result, ("admissionSummary", "hospitalCourse", "dischargeDiagnosis", "followUpInstructions", "conditionAtDischarge")
        )
        result["medicationsAtDischarge"] = drug_matcher.correct_medication_names(result["medicationsAtDischarge"])
        return result

    def is_available(self) -> bool:
        if not self.api_key:
            return False
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            resp = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.error("is_available exception: %s", e)
            return False

    def pull_model(self) -> bool:
        return True

scribe = ScribeEngine()