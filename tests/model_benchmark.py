import os
import sys
import json
import time
import requests

sys.path.insert(0, os.path.abspath('.'))

from dotenv import load_dotenv
load_dotenv('backend/.env')

from backend.app.config import settings
from backend.app.scribe import ScribeEngine, scribe
from backend.app.tasks_engine import check_drug_interactions

# The viable general-purpose LLMs on your Groq key
CANDIDATES = [
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b"
]

TEST_TRANSCRIPT_1 = (
    "Doctor: Good morning Ramesh, what brings you in today? "
    "Patient: Doctor, I have had high fever with chills since 3 days and severe dry cough. "
    "Also feeling extreme fatigue and mild chest congestion. "
    "Doctor: Let me check. Your throat looks inflamed, chest has mild bilateral wheeze. "
    "This looks like Acute Bronchitis with secondary bacterial infection. "
    "I am prescribing Tab Azithromycin 500mg once daily for 5 days after food. "
    "Also Tab Paracetamol 650mg three times a day for fever as needed for 3 days. "
    "Take Syrup Ascoril D 10ml thrice daily for 5 days for the dry cough. "
    "Do steam inhalation twice a day, drink plenty of warm water, and avoid cold drinks. "
    "Please get a Complete Blood Count (CBC) and Chest X-ray done if fever persists beyond 48 hours."
)

TEST_VITALS_VOICE = (
    "BP is 138 over 88, pulse rate 84 beats per minute, temperature 99.4 Fahrenheit, "
    "oxygen saturation is 97 percent on room air, respiratory rate 18 per minute. Patient alert and oriented."
)

TEST_SOAP_VOICE = (
    "Patient complaining of persistent dull aching lower back pain radiating to left leg for 2 days. "
    "On exam, straight leg raise positive on left at 45 degrees, tenderness at L4-L5 region. Vitals stable. "
    "Assessment: Lumbar radiculopathy likely L4-L5 disc prolapse. "
    "Plan: Bed rest for 2 days, start muscle relaxants and NSAIDs, schedule MRI lumbosacral spine."
)

TEST_DISCHARGE_CONTEXT = {
    "patient_name": "Sunita Sharma",
    "age": 48,
    "gender": "Female",
    "ward": "Female Medical Ward",
    "admission_date": "2026-08-10",
    "diagnosis": "Community Acquired Pneumonia",
    "vitals": [
        {"recorded_at": "2026-08-10T10:00:00", "bp_systolic": 110, "bp_diastolic": 70, "heart_rate": 102, "temperature": 39.1, "oxygen_sat": 89, "respiratory_rate": 26, "notes": "Fever, tachypnea"},
        {"recorded_at": "2026-08-14T08:00:00", "bp_systolic": 120, "bp_diastolic": 80, "heart_rate": 76, "temperature": 36.8, "oxygen_sat": 98, "respiratory_rate": 16, "notes": "Stable, afebrile"}
    ],
    "nursing_notes": [
        {"created_at": "2026-08-10T11:00:00", "notes": "IV Ceftriaxone initiated. Oxygen at 2L via nasal cannula."},
        {"created_at": "2026-08-14T09:00:00", "notes": "Patient afebrile for 48h. Ambulated without dyspnea. Switched to oral antibiotics."}
    ],
    "tasks": [
        {"description": "Administer IV Ceftriaxone 1g BD", "status": "Completed"},
        {"description": "Chest Physiotherapy", "status": "Completed"}
    ],
    "consultations": [
        {"chief_complaint": "High fever, productive cough, breathlessness", "primary_diagnosis": "Community Acquired Pneumonia (Right lower lobe)", "medications": [{"drugName": "Augmentin 625mg", "dose": "625mg", "frequency": "BD", "duration": "5 days"}], "advice": "Complete oral antibiotic course"}
    ]
}

def benchmark_model(model_name):
    print(f"\n{'='*60}", flush=True)
    print(f"BENCHMARKING MODEL: {model_name}", flush=True)
    print(f"{'='*60}", flush=True)
    results = {
        "model": model_name,
        "scribe_latency": 0.0,
        "scribe_success": False,
        "scribe_meds_count": 0,
        "scribe_labs_count": 0,
        "vitals_latency": 0.0,
        "vitals_success": False,
        "soap_latency": 0.0,
        "soap_success": False,
        "discharge_latency": 0.0,
        "discharge_success": False,
        "interaction_latency": 0.0,
        "interaction_success": False,
        "total_latency": 0.0
    }
    
    engine = ScribeEngine()
    engine.model = model_name
    scribe.model = model_name  # sync global instance
    
    start_total = time.time()

    # 1. Test OPD Scribe
    try:
        t0 = time.time()
        scribe_res = engine.scribe_transcript(TEST_TRANSCRIPT_1)
        t1 = time.time()
        results["scribe_latency"] = round(t1 - t0, 3)
        
        meds = scribe_res.get("medications", [])
        labs = scribe_res.get("labTests", [])
        diag = scribe_res.get("primaryDiagnosis", "")
        cc = scribe_res.get("chiefComplaint", "")
        
        results["scribe_meds_count"] = len(meds)
        results["scribe_labs_count"] = len(labs)
        results["scribe_success"] = bool(diag and len(meds) >= 2 and cc)
        
        print(f"  [1/5 OPD Scribe] Latency: {results['scribe_latency']}s | Diagnosis: '{diag}' | Meds: {len(meds)} | Labs: {len(labs)}", flush=True)
    except Exception as e:
        print(f"  [1/5 OPD Scribe] FAILED: {e}", flush=True)

    # 2. Test Vitals Voice Extraction
    try:
        t0 = time.time()
        vitals_prompt = f'Extract vital signs from the following nurse\'s voice note and return as JSON:\n"{TEST_VITALS_VOICE}"\nReturn JSON with fields: bp_systolic, bp_diastolic, heart_rate, temperature, oxygen_sat, respiratory_rate, notes.'
        vitals_res = engine._generate_json(vitals_prompt, temperature=0.2)
        t1 = time.time()
        results["vitals_latency"] = round(t1 - t0, 3)
        results["vitals_success"] = bool(vitals_res.get("bp_systolic") and vitals_res.get("heart_rate"))
        print(f"  [2/5 Vitals Extraction] Latency: {results['vitals_latency']}s | BP: {vitals_res.get('bp_systolic')}/{vitals_res.get('bp_diastolic')} | HR: {vitals_res.get('heart_rate')} | Temp: {vitals_res.get('temperature')} | SpO2: {vitals_res.get('oxygen_sat')}", flush=True)
    except Exception as e:
        print(f"  [2/5 Vitals Extraction] FAILED: {e}", flush=True)

    # 3. Test SOAP Nursing Note Extraction
    try:
        t0 = time.time()
        soap_prompt = f'Given the following voice transcription, produce a structured nursing note in SOAP format: Subjective, Objective, Assessment, Plan.\nReturn as JSON with keys: subjective, objective, assessment, plan.\nVoice transcript: "{TEST_SOAP_VOICE}"'
        soap_res = engine._generate_json(soap_prompt, temperature=0.2)
        t1 = time.time()
        results["soap_latency"] = round(t1 - t0, 3)
        results["soap_success"] = bool(soap_res.get("assessment") and soap_res.get("plan"))
        print(f"  [3/5 SOAP Note] Latency: {results['soap_latency']}s | Assessment: {bool(soap_res.get('assessment'))} | Plan: {bool(soap_res.get('plan'))}", flush=True)
    except Exception as e:
        print(f"  [3/5 SOAP Note] FAILED: {e}", flush=True)

    # 4. Test Discharge Summary
    try:
        t0 = time.time()
        ds_res = engine.generate_discharge_summary(TEST_DISCHARGE_CONTEXT)
        t1 = time.time()
        results["discharge_latency"] = round(t1 - t0, 3)
        results["discharge_success"] = bool(ds_res.get("dischargeDiagnosis") and ds_res.get("hospitalCourse"))
        print(f"  [4/5 Discharge Summary] Latency: {results['discharge_latency']}s | Diag: '{ds_res.get('dischargeDiagnosis')}' | Meds: {len(ds_res.get('medicationsAtDischarge', []))}", flush=True)
    except Exception as e:
        print(f"  [4/5 Discharge Summary] FAILED: {e}", flush=True)

    # 5. Test Drug Interactions
    try:
        t0 = time.time()
        inter = check_drug_interactions(["Warfarin", "Aspirin", "Clopidogrel"])
        t1 = time.time()
        results["interaction_latency"] = round(t1 - t0, 3)
        results["interaction_success"] = len(inter) > 0
        print(f"  [5/5 Drug Interactions] Latency: {results['interaction_latency']}s | Detected: {len(inter)} interactions", flush=True)
    except Exception as e:
        print(f"  [5/5 Drug Interactions] FAILED: {e}", flush=True)

    results["total_latency"] = round(time.time() - start_total, 3)
    return results

if __name__ == "__main__":
    summary = []
    for model in CANDIDATES:
        res = benchmark_model(model)
        summary.append(res)
    
    print("\n" + "="*80, flush=True)
    print("FINAL BENCHMARK COMPARISON TABLE", flush=True)
    print("="*80, flush=True)
    header = f"{'Model':<22} | {'OPD Scribe':<10} | {'Vitals':<8} | {'SOAP':<8} | {'Discharge':<10} | {'Interactions':<12} | {'Total Time':<10}"
    print(header, flush=True)
    print("-" * len(header), flush=True)
    for s in summary:
        row = f"{s['model']:<22} | {s['scribe_latency']:<6}s {'✓' if s['scribe_success'] else '✗'} | {s['vitals_latency']:<5}s {'✓' if s['vitals_success'] else '✗'} | {s['soap_latency']:<5}s {'✓' if s['soap_success'] else '✗'} | {s['discharge_latency']:<6}s {'✓' if s['discharge_success'] else '✗'} | {s['interaction_latency']:<7}s {'✓' if s['interaction_success'] else '✗'} | {s['total_latency']:<6}s"
        print(row, flush=True)
