// Records microphone audio via MediaRecorder and uploads it to POST {apiBase}/transcribe-audio
// for server-side transcription (backend/app/main.py's transcribe_audio_endpoint). `provider`
// (fetch GET {apiBase}/transcription-provider to find out which one the backend is configured
// for) only selects which backend the recording is sent to -- both record the SAME way: ONE
// continuous blob for the whole session, uploaded once at stop().
//
//   - "whisper" (default): Groq Whisper. Original behavior, unchanged since it replaced the
//     browser's built-in SpeechRecognition (which mis-transcribed Hindi/Hinglish speech into
//     English-phonetic nonsense -- a real drug name once came out as unrelated English words).
//   - "sarvam": Sarvam AI's Saaras v3, via backend/app/sarvam_batch_transcriber.py's Batch
//     Speech-to-Text API integration, which accepts a single file up to 2 hours long -- so
//     unlike Sarvam's 30-second-capped REST endpoint (used by this file's own now-removed
//     rolling-chunk-restart mode, kept only in git history), no client-side chunking is needed
//     here either.
//
// There is no "live caption" concept in either mode: only the final transcript comes back,
// after stop() uploads the whole recording.
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
//   recorder.wasInterrupted();         // true if stop() resolved after a mid-recording error
//                                       // (device disconnect, encoder failure) rather than a
//                                       // normal doctor-initiated stop -- check after stop()
//                                       // resolves and warn that the transcript may be short.

function createVoiceRecorder({ apiBase, getAuthToken, provider }) {
    // `provider` is accepted (callers fetch it from GET {apiBase}/transcription-provider) but
    // no longer changes recording behavior here -- both providers now record one continuous
    // blob and let the server (main.py's transcribe_audio_endpoint, keyed off
    // settings.TRANSCRIPTION_PROVIDER) decide which backend handles it. Kept as a parameter so
    // existing callers don't need to change.
    void provider;

    // Chrome/Firefox produce webm; Safari doesn't support webm at all and needs mp4. Both
    // Groq's and Sarvam's accepted upload formats include webm/mp4, so no client-side
    // transcoding is needed -- just use whatever the browser can actually produce, in
    // accuracy-preference order.
    const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];

    // Without this, MediaRecorder picks its own default bitrate for audio-only capture (~128
    // kbps on Chrome) -- verified live: a genuine 30-minute recording at that default bitrate
    // exceeded main.py's MAX_AUDIO_UPLOAD_BYTES (25MB) and the upload failed outright with 413
    // "Audio file too large", losing the whole recording (no partial save, no retry) despite the
    // recording itself having succeeded -- exactly the class of long-recording failure this
    // whole pipeline exists to avoid. 24 kbps mono Opus is comfortably clear for speech/STT
    // purposes (nowhere near music-quality bitrates) and keeps even a full 2-hour recording
    // (Sarvam Batch STT's own ceiling, see sarvam_batch_transcriber.py) to ~21.6MB, safely under
    // the 25MB cap with headroom for encoder variance -- so the cap itself doesn't need raising,
    // the bitrate just needed to actually be bounded.
    const AUDIO_BITS_PER_SECOND = 24000;

    let stream = null;
    let audioCtx = null;
    let analyser = null;
    let levelData = null;
    let sessionMimeType = '';

    let mediaRecorder = null;
    let chunks = [];
    // Set when the recorder errors out or stops on its own AFTER recording has actually
    // started (device disconnect, encoder failure, OS-level interruption) -- as opposed to a
    // normal doctor-initiated stop(). Checked by wasInterrupted() after stop() resolves, so a
    // caller can warn that the returned transcript may be shorter than the real conversation,
    // instead of silently presenting a partial recording as if it were complete.
    let recordingInterrupted = false;

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
    // AnalyserNode, independent of the MediaRecorder itself, so it keeps working the same way
    // regardless of provider. Returns 0 when not recording or before the first analyser frame
    // is available.
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

    // --- one continuous recording for the whole session, for either provider ---

    async function _startRecording() {
        chunks = [];
        recordingInterrupted = false;
        const recorderOptions = { audioBitsPerSecond: AUDIO_BITS_PER_SECOND };
        if (sessionMimeType) recorderOptions.mimeType = sessionMimeType;
        mediaRecorder = new MediaRecorder(stream, recorderOptions);
        mediaRecorder.ondataavailable = (e) => {
            if (e.data && e.data.size > 0) chunks.push(e.data);
        };
        const started = new Promise((resolve, reject) => {
            mediaRecorder.onstart = resolve;
            mediaRecorder.onerror = (e) => reject(e.error || new Error('Recording failed to start'));
        });
        mediaRecorder.start();
        await started;
        // The `onerror` above only ever settles the one-time `started` promise -- reassigning
        // it here matters because a promise can only settle once: an error AFTER this point
        // (device disconnect, encoder failure mid-recording) would call that same `reject` into
        // an already-resolved promise, which is a silent no-op. Previously that meant a
        // mid-recording failure produced a shorter-than-real transcript with zero indication
        // anything had gone wrong. Flag it instead so stop()/wasInterrupted() can tell the caller.
        mediaRecorder.onerror = () => { recordingInterrupted = true; };
    }

    function _stopRecording() {
        return new Promise((resolve, reject) => {
            if (!mediaRecorder) {
                reject(new Error('Not recording'));
                return;
            }
            const recorder = mediaRecorder;
            mediaRecorder = null;
            const finish = async () => {
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
            if (recorder.state === 'inactive') {
                // Already stopped on its own (a mid-recording error stops the recorder before
                // the doctor ever clicks Stop) -- recorder.stop() would throw InvalidStateError
                // here instead of firing onstop. Whatever chunks were captured before the error
                // are still real audio; finish with those rather than losing the recording
                // entirely.
                recordingInterrupted = true;
                finish();
                return;
            }
            recorder.onstop = finish;
            recorder.stop();
        });
    }

    // --- shared upload path ---

    async function _uploadAndTranscribe(blobs) {
        const form = new FormData();
        blobs.forEach((blob, i) => {
            const ext = extForMimeType(blob.type);
            form.append('audio', blob, `chunk_${i}.${ext}`);
        });

        // Deliberately NOT going through this page's JSON apiRequest()/Api.upload() helpers --
        // they hardcode 'Content-Type: application/json' as a header default (apiRequest) or
        // otherwise don't fit here cleanly, which is incompatible with FormData (the browser
        // must set the multipart boundary itself, which only happens when no Content-Type is
        // set at all). FormData is safe to resend unchanged on a retry -- fetch reads it when
        // building the request, it isn't consumed/mutated by being sent once.
        const doUpload = () => {
            const headers = {};
            const token = getAuthToken && getAuthToken();
            if (token) headers['Authorization'] = `Bearer ${token}`;
            return fetch(`${apiBase}/transcribe-audio`, { method: 'POST', headers, body: form });
        };

        let res = await doUpload();
        // The access token is short-lived by design (15 min server-side) -- a recording that
        // runs longer than that (exactly the long-consultation case this whole pipeline exists
        // for) would otherwise ALWAYS fail here with 401 right as the doctor stops recording,
        // losing the entire transcript despite the recording itself having succeeded. Verified
        // live: a 20-minute recording's upload hit exactly this. Every other authenticated call
        // in this app (Api.get/post/upload, frontend/js/api.js) already retries once after a
        // token refresh on 401 -- this mirrors that same pattern (reusing api.js's shared
        // _refreshAccessToken(), a plain global function since both files load as classic
        // scripts in the same page) rather than inventing a second one.
        if (res.status === 401 && typeof _refreshAccessToken === 'function' && typeof Auth !== 'undefined' && Auth.getRefreshToken && Auth.getRefreshToken()) {
            try {
                await _refreshAccessToken();
                res = await doUpload();
            } catch (err) {
                // Refresh itself failed (refresh token also expired/invalid) -- fall through to
                // the normal error handling below, which surfaces the (still-401) response.
            }
        }

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
            await _startRecording();
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
        return _stopRecording();
    }

    // True if the recording ended early on its own (device disconnect, encoder failure) rather
    // than because the caller invoked stop() on a still-healthy recording. Meaningful only
    // after stop() has resolved -- callers that care should warn the transcript may be
    // incomplete rather than presenting it as a normal, complete recording.
    function wasInterrupted() {
        return recordingInterrupted;
    }

    return { start, stop, isSupported, getLevel, wasInterrupted };
}
