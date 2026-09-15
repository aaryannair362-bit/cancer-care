"""
Standalone child-process TTS worker for audio_duration_runner.py.

Runs pyttsx3.runAndWait() in its OWN process, invoked via subprocess.run(..., timeout=...) from
the parent -- found necessary live: feeding the installed Windows SAPI5 English voice (no Hindi
voice is installed on this machine -- see audio_duration_runner.py's content-pool Devanagari
filter, which now excludes this content anyway) text containing Devanagari script caused
engine.runAndWait() to hang indefinitely (confirmed: the process sat at ~0% CPU, never returned,
had to be killed manually). A same-process call has no way to recover from that; isolating the
call in a disposable child process the parent can kill on timeout means one bad segment costs one
timeout, not the entire multi-hundred-case run.

Usage: python _tts_worker.py <input_text_path> <output_wav_path>
"""
import sys


def main():
    text_path, wav_path = sys.argv[1], sys.argv[2]
    with open(text_path, "r", encoding="utf-8") as f:
        text = f.read()

    import pyttsx3
    engine = pyttsx3.init()
    engine.save_to_file(text, wav_path)
    engine.runAndWait()


if __name__ == "__main__":
    main()
