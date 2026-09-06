// Records microphone audio via MediaRecorder and uploads it to POST {apiBase}/transcribe-audio
// for server-side transcription (backend/app/main.py's transcribe_audio_endpoint). Two
// incompatible recording strategies, selected by `provider` (fetch GET
// {apiBase}/transcription-provider to find out which one the backend is configured for --
// see that endpoint's docstring):
//
//   - "whisper" (default): records ONE continuous blob for the whole session, uploaded once
//     at stop(). Original behavior, unchanged since it replaced the browser's built-in
//     SpeechRecognition (which mis-transcribed Hindi/Hinglish speech into English-phonetic
//     nonsense -- a real drug name once came out as unrelated English words).
//   - "sarvam": Sarvam AI's Saaras v3 REST endpoint enforces a hard 30-second-per-request cap
//     (verified live against the real API before this was built), so a multi-minute
//     consultation can't be uploaded as one file. Instead this restarts MediaRecorder on a
//     rolling ~25s cadence, producing several complete, independently-valid audio files
//     (NOT relying on MediaRecorder's `timeslice` option -- a timesliced chunk isn't reliably
//     decodable on its own in every browser, only the first one carries the container header;
//     restarting the recorder sidesteps that entirely, since start()/stop() always produces a
//     complete file by construction), all uploaded together at stop() under repeated "audio"
//     form fields. backend/app/sarvam_transcriber.py transcribes each and joins the results.
//
// There is no "live caption" concept in either mode: only the final joined transcript comes
// back, after stop() uploads everything.
//
// Shared by frontend/opd.html, frontend/ipd.html (nursing note + ward round), and
// frontend/headnurse.html -- plain <script src="/js/voice-capture.js">, no build step/module
// system, matching this app's existing frontend style (see frontend/js/ipd-shared.js). Pages
// that don't pass `provider` get "whisper" -- today's exact behavior, unchanged.
//
// Usage:
//   const recorder = createVoiceRecorder({ apiBase: API_BASE, getAuthToken: () => accessToken, provider: 'whisper' });
//   if (!recorder.isSupported()) { /* fall back to manual typing, same as before */ }
//   await recorder.start();            // throws if mic permission denied
//   const transcript = await recorder.stop();   // throws if upload/transcription fails
//   recorder.getLevel();               // 0-1 live mic input level while recording, see below

function createVoiceRecorder({ apiBase, getAuthToken, provider }) {
    // Chrome/Firefox produce webm; Safari doesn't support webm at all and needs mp4. Both
    // Groq's and Sarvam's accepted upload formats include webm/mp4, so no client-side
    // transcoding is needed -- just use whatever the browser can actually produce, in
    // accuracy-preference order.
    const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
    // Comfortably under Sarvam's hard 30s-per-request cap (verified live) -- 25s leaves margin
    // for the rotation call itself and any small timing drift, rather than cutting it exactly
    // at the boundary where a slightly-late rotation could produce a rejected >30s chunk.
    const SARVAM_CHUNK_MS = 25000;

    const effectiveProvider = provider === 'sarvam' ? 'sarvam' : 'whisper';

    let stream = null;
    let audioCtx = null;
    let analyser = null;
    let levelData = null;
    let sessionMimeType = '';

    // "whisper" mode state
    let mediaRecorder = null;
    let chunks = [];

    // "sarvam" mode state
    let currentChunkRecorder = null;
    let chunkFinishedPromises = [];
    let rotationTimer = null;

    function isSupported() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
    }

    function pickMimeType() {
        if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return '';
        return MIME_CANDIDATES.find((t) => MediaRecorder.isTypeSupported(t)) || '';
    }

    function extForMimeType(mimeType) {
        const mt = mimeType || 'audio/webm';
        return mt.includes('mp4') ? 'mp4' : mt.includes('ogg') ? 'ogg' : 'webm';
    }

    // Live 0-1 input level (RMS of the current time-domain buffer), meant to be polled from a
    // requestAnimationFrame loop while recording to drive a visual "yes, I can hear you" meter.
    // Not transcription feedback -- purely a signal that the mic is actually picking up sound
    // as the doctor talks, added because without ANY feedback during the silent recording
    // phase, doctors reported having to speak unnaturally slowly/over-enunciate out of
    // uncertainty that anything was being captured at all. Taps `stream` directly via its own
    // AnalyserNode, independent of which MediaRecorder (whisper's single one, or sarvam's
    // rotating ones) is currently active, so provider switching doesn't affect it at all.
    // Returns 0 when not recording or before the first analyser frame is available.
    function getLevel() {
        if (!analyser || !levelData) return 0;
        analyser.getByteTimeDomainData(levelData);
        let sumSquares = 0;
        for (let i = 0; i < levelData.length; i++) {
            const centered = (levelData[i] - 128) / 128;
            sumSquares += centered * centered;
        }
        return Math.min(1, Math.sqrt(sumSquares / levelData.length) * 4);
    }

    function _setUpLevelMeter() {
        // Best-effort: a browser without Web Audio API (or one that throws on construction)
        // just means getLevel() always returns 0 -- the recording/transcription path must not
        // be affected by it either way.
        try {
            const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
            audioCtx = new AudioContextCtor();
            const source = audioCtx.createMediaStreamSource(stream);
            analyser = audioCtx.createAnalyser();
            analyser.fftSize = 256;
            levelData = new Uint8Array(analyser.frequencyBinCount);
            // Deliberately NOT connected to audioCtx.destination -- this taps the stream for
            // level metering only; routing it to output would echo the doctor's own mic back
            // through their speakers.
            source.connect(analyser);
        } catch (err) {
            audioCtx = null;
            analyser = null;
            levelData = null;
        }
    }

    function _tearDownLevelMeter() {
        if (audioCtx) {
            audioCtx.close().catch(() => {});
        }
        audioCtx = null;
        analyser = null;
        levelData = null;
    }

    // --- "whisper" mode: one continuous recording for the whole session ---

    async function _startWhisper() {
        chunks = [];
        mediaRecorder = sessionMimeType ? new MediaRecorder(stream, { mimeType: sessionMimeType }) : new MediaRecorder(stream);
        mediaRecorder.ondataavailable = (e) => {
            if (e.data && e.data.size > 0) chunks.push(e.data);
        };
        const started = new Promise((resolve, reject) => {
            mediaRecorder.onstart = resolve;
            mediaRecorder.onerror = (e) => reject(e.error || new Error('Recording failed to start'));
        });
        mediaRecorder.start();
        await started;
    }

    function _stopWhisper() {
        return new Promise((resolve, reject) => {
            if (!mediaRecorder) {
                reject(new Error('Not recording'));
                return;
            }
            const recorder = mediaRecorder;
            mediaRecorder = null;
            recorder.onstop = async () => {
                if (stream) {
                    stream.getTracks().forEach((t) => t.stop());
                    stream = null;
                }
                if (chunks.length === 0) {
                    resolve('');
                    return;
                }
                const mimeType = recorder.mimeType || 'audio/webm';
                const blob = new Blob(chunks, { type: mimeType });
                chunks = [];
                try {
                    resolve(await _uploadAndTranscribe([blob]));
                } catch (err) {
                    reject(err);
                }
            };
            recorder.stop();
        });
    }

    // --- "sarvam" mode: restart the recorder on a rolling cadence, upload all chunks at stop() ---

    function _startOneChunkRecorder() {
        const rec = sessionMimeType ? new MediaRecorder(stream, { mimeType: sessionMimeType }) : new MediaRecorder(stream);
        const pieces = [];
        rec.ondataavailable = (e) => {
            if (e.data && e.data.size > 0) pieces.push(e.data);
        };
        const finished = new Promise((resolve) => {
            rec.onstop = () => {
                if (pieces.length === 0) {
                    resolve(null);
                    return;
                }
                resolve(new Blob(pieces, { type: rec.mimeType || sessionMimeType || 'audio/webm' }));
            };
            // A mid-rotation error just loses that one chunk's audio (resolved as null,
            // filtered out before upload) rather than failing the whole consultation --
            // matches sarvam_transcriber.py's "skip a failed chunk, don't lose everything"
            // philosophy on the backend side of this same tradeoff.
            rec.onerror = () => resolve(null);
        });
        return { rec, finished };
    }

    async function _startSarvam() {
        chunkFinishedPromises = [];
        const first = _startOneChunkRecorder();
        const started = new Promise((resolve, reject) => {
            first.rec.onstart = resolve;
            first.rec.onerror = (e) => reject(e.error || new Error('Recording failed to start'));
        });
        currentChunkRecorder = first.rec;
        chunkFinishedPromises.push(first.finished);
        first.rec.start();
        await started;

        rotationTimer = setInterval(() => {
            const old = currentChunkRecorder;
            if (!old) return;
            old.stop();
            const next = _startOneChunkRecorder();
            currentChunkRecorder = next.rec;
            chunkFinishedPromises.push(next.finished);
            next.rec.start();
        }, SARVAM_CHUNK_MS);
    }

    async function _stopSarvam() {
        if (!currentChunkRecorder) {
            throw new Error('Not recording');
        }
        if (rotationTimer) {
            clearInterval(rotationTimer);
            rotationTimer = null;
        }
        currentChunkRecorder.stop();
        currentChunkRecorder = null;

        const blobs = (await Promise.all(chunkFinishedPromises)).filter((b) => b !== null);
        chunkFinishedPromises = [];

        if (stream) {
            stream.getTracks().forEach((t) => t.stop());
            stream = null;
        }
        if (blobs.length === 0) {
            return '';
        }
        return _uploadAndTranscribe(blobs);
    }

    // --- shared upload path ---

    async function _uploadAndTranscribe(blobs) {
        const form = new FormData();
        blobs.forEach((blob, i) => {
            const ext = extForMimeType(blob.type);
            form.append('audio', blob, `chunk_${i}.${ext}`);
        });
        const headers = {};
        const token = getAuthToken && getAuthToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        // Deliberately NOT going through this page's JSON apiRequest() helper -- it hardcodes
        // 'Content-Type: application/json' as a header default, which is incompatible with
        // FormData (the browser must set the multipart boundary itself, which only happens
        // when no Content-Type is set at all).
        const res = await fetch(`${apiBase}/transcribe-audio`, { method: 'POST', headers, body: form });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || `Transcription failed (${res.status})`);
        }
        const data = await res.json();
        return data.transcript || '';
    }

    // --- public interface ---

    async function start() {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        sessionMimeType = pickMimeType();
        _setUpLevelMeter();
        try {
            if (effectiveProvider === 'sarvam') {
                await _startSarvam();
            } else {
                await _startWhisper();
            }
        } catch (err) {
            _tearDownLevelMeter();
            if (stream) {
                stream.getTracks().forEach((t) => t.stop());
                stream = null;
            }
            throw err;
        }
    }

    async function stop() {
        _tearDownLevelMeter();
        if (effectiveProvider === 'sarvam') {
            return _stopSarvam();
        }
        return _stopWhisper();
    }

    return { start, stop, isSupported, getLevel };
}
