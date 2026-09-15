// Records microphone audio via MediaRecorder and uploads it to POST {apiBase}/transcribe-audio-chunk
// for server-side transcription (backend/app/main.py's transcribe_audio_chunk_endpoint).
// `provider` (fetch GET {apiBase}/transcription-provider to find out which one the backend is
// configured for) only selects which backend the recording is sent to -- both record the SAME
// way, in periodic chunks, for the whole session.
//
//   - "whisper" (default): Groq Whisper. Original behavior, unchanged since it replaced the
//     browser's built-in SpeechRecognition (which mis-transcribed Hindi/Hinglish speech into
//     English-phonetic nonsense -- a real drug name once came out as unrelated English words).
//   - "sarvam": Sarvam AI's Saaras v3, via backend/app/sarvam_batch_transcriber.py's Batch
//     Speech-to-Text API integration.
//
// CHUNKED, not one continuous upload at Stop (as of 2026-09) -- a real production bug, not just
// a latency concern: during recording, MediaRecorder captures entirely client-side, so the
// browser made ZERO backend requests until Stop. A consultation running past 15 minutes of
// silence got the Render service (free tier: scales to zero after 15 min with no inbound
// traffic) spun down mid-recording -- confirmed from real production logs -- so the
// Stop-triggered upload hit a cold, sleeping container, independent of anything in the
// Sarvam/Groq pipeline itself. Every CHUNK_INTERVAL_MS, the current MediaRecorder is stopped
// and a NEW one immediately started on the SAME stream (not re-acquiring getUserMedia) --
// this produces an independently-valid, self-contained WebM/mp4 file each time, unlike a
// `timeslice` fragment (which isn't independently decodable after the first piece). Each
// finished chunk is uploaded and transcribed as soon as it's ready, not batched until Stop --
// that's what actually keeps real traffic flowing during the recording. At Stop, the final
// (possibly short) chunk is uploaded with is_final=true; the server concatenates every
// chunk's transcript IN ORDER (by explicit index, not arrival order -- uploads can complete
// out of order over the network) and returns the full transcript in the same {"transcript":...}
// shape as before, so this file's own public stop() contract is unchanged -- every calling page
// still just gets one transcript string back.
//
// Deliberately NOT the old (since-removed, see git history commit cd20eea) rolling-chunk-restart
// mode's design: that one ALSO restarted the recorder periodically, but only to stay under
// Sarvam's old 30-second REST cap -- it still held every chunk client-side and uploaded them
// all together in ONE request at Stop, which did nothing to prevent the Render-sleep failure
// above, and was hard-capped at MAX_AUDIO_CHUNKS=40 server-side, silently losing the ENTIRE
// transcript past ~16.7 minutes. This design uploads each chunk as it's ready and has no chunk
// count ceiling -- a long recording just produces more chunks.
//
// There is no "live caption" concept: only the final transcript comes back, after stop()
// uploads the last chunk and the server joins everything together.
//
// Shared by frontend/opd.html, frontend/ipd.html (nursing note + ward round),
// frontend/headnurse.html, frontend/medical_oncologist.html, frontend/radiation_oncologist.html,
// and frontend/surgical_oncologist.html -- plain <script src="/js/voice-capture.js">, no build
// step/module system, matching this app's existing frontend style (see
// frontend/js/ipd-shared.js). Pages that don't pass `provider` get "whisper" -- today's exact
// behavior, unchanged.
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
//   recorder.wasPartial();             // true if stop() resolved but at least one mid-recording
//                                       // chunk failed to transcribe server-side (Groq/Sarvam
//                                       // error on that chunk) -- the returned transcript is
//                                       // real but may be missing a slice of the conversation.

function createVoiceRecorder({ apiBase, getAuthToken, provider, chunkIntervalMs }) {
    // `provider` is accepted (callers fetch it from GET {apiBase}/transcription-provider) but
    // no longer changes recording behavior here -- both providers record the same chunked way
    // and let the server (main.py's transcribe_audio_chunk_endpoint, keyed off
    // settings.TRANSCRIPTION_PROVIDER) decide which backend handles each chunk. Kept as a
    // parameter so existing callers don't need to change.
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
    // the bitrate just needed to actually be bounded. Per-chunk (not per-recording) now, but the
    // same math applies at any chunk length: comfortably small.
    const AUDIO_BITS_PER_SECOND = 24000;

    // 3 minutes: comfortably under Render's 15-minute inactivity-sleep window (wide margin for
    // network/processing jitter between chunks), while staying low enough that Sarvam Batch
    // STT's per-job overhead (create/upload/start/poll/download -- see
    // sarvam_batch_transcriber.py) doesn't dominate the cost of transcribing each chunk.
    // Overridable via createVoiceRecorder's own `chunkIntervalMs` option so tests can drive
    // rotation cycles without waiting 3 real minutes -- production callers simply never pass it.
    const CHUNK_INTERVAL_MS = chunkIntervalMs || 3 * 60 * 1000;

    let stream = null;
    let audioCtx = null;
    let analyser = null;
    let levelData = null;
    let sessionMimeType = '';

    let mediaRecorder = null;
    let chunks = [];
    let sessionId = '';
    let nextChunkIndex = 0;
    let rotationTimer = null;
    // Fire-and-forget mid-recording chunk uploads aren't awaited by the caller (nothing should
    // block the doctor's live recording on a background upload), but stop() still waits for all
    // of them to at least finish being SENT (not necessarily transcribed) before uploading the
    // final chunk, so chunk_index ordering on the server side is never violated by a final
    // upload racing ahead of an earlier one still in flight.
    let pendingChunkUploads = [];
    // Set when the recorder errors out or stops on its own AFTER recording has actually
    // started (device disconnect, encoder failure, OS-level interruption) -- as opposed to a
    // normal doctor-initiated stop(). Checked by wasInterrupted() after stop() resolves, so a
    // caller can warn that the returned transcript may be shorter than the real conversation,
    // instead of silently presenting a partial recording as if it were complete.
    let recordingInterrupted = false;
    // Set from the final chunk upload's own response (server-computed -- see
    // transcribe_audio_chunk_endpoint's transcriptPartial) if any earlier chunk in this session
    // failed to transcribe. Distinct from recordingInterrupted: the recording itself completed
    // normally, but a slice of it may be missing from the transcript.
    let sessionPartial = false;

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

    function _newSessionId() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
        // Fallback for a browser/test environment without crypto.randomUUID -- doesn't need to
        // be cryptographically strong, only unique enough to key one in-memory server dict entry
        // per recording session.
        return `sess-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    // Live 0-1 input level (RMS of the current time-domain buffer), meant to be polled from a
    // requestAnimationFrame loop while recording to drive a visual "yes, I can hear you" meter.
    // Not transcription feedback -- purely a signal that the mic is actually picking up sound
    // as the doctor talks, added because without ANY feedback during the silent recording
    // phase, doctors reported having to speak unnaturally slowly/over-enunciate out of
    // uncertainty that anything was being captured at all. Taps `stream` directly via its own
    // AnalyserNode, independent of any particular MediaRecorder instance, so periodic recorder
    // rotation never disturbs it. Returns 0 when not recording or before the first analyser
    // frame is available.
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

    // --- one MediaRecorder instance at a time, restarted every CHUNK_INTERVAL_MS ---

    async function _startOneRecorder() {
        chunks = [];
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

    // Stops the CURRENT recorder and resolves with its finished Blob (or null if it captured
    // nothing) -- shared by both the periodic rotation and the final Stop flush. Never re-
    // acquires getUserMedia; the underlying `stream` stays open and is reused by whatever
    // recorder starts next (rotation) or is torn down entirely (real Stop).
    function _flushCurrentRecorder() {
        return new Promise((resolve) => {
            if (!mediaRecorder) {
                resolve(null);
                return;
            }
            const recorder = mediaRecorder;
            mediaRecorder = null;
            const finish = () => {
                if (chunks.length === 0) {
                    resolve(null);
                    return;
                }
                const mimeType = recorder.mimeType || 'audio/webm';
                const blob = new Blob(chunks, { type: mimeType });
                chunks = [];
                resolve(blob);
            };
            if (recorder.state === 'inactive') {
                // Already stopped on its own (a mid-recording error stops the recorder before
                // the doctor/rotation timer ever calls stop()) -- recorder.stop() would throw
                // InvalidStateError here instead of firing onstop. Whatever chunks were
                // captured before the error are still real audio; finish with those rather
                // than losing them entirely.
                recordingInterrupted = true;
                finish();
                return;
            }
            recorder.onstop = finish;
            recorder.stop();
        });
    }

    // Flushes the current recorder, uploads its blob as a non-final chunk (fire-and-forget from
    // the recording's own perspective -- the mic keeps capturing via the newly-started recorder
    // while this upload happens in the background), and starts a fresh recorder on the same
    // stream. A chunk that captured nothing (silence-only interval) is simply skipped -- no
    // point uploading/transcribing an empty blob.
    async function _rotateChunk() {
        const blob = await _flushCurrentRecorder();
        const index = nextChunkIndex++;
        if (blob) {
            const uploadPromise = _uploadChunk(blob, index, false).catch(() => {
                // A single failed chunk upload must never crash the recording or surface to the
                // doctor mid-consultation -- the server already treats a missing chunk_index as
                // "dropped, not fatal" (see transcribe_audio_chunk_endpoint) and flags
                // transcriptPartial on the final response instead.
            });
            pendingChunkUploads.push(uploadPromise);
        }
        // Only start a new recorder if the session is still active (stop() may have already
        // nulled `stream` out from under a rotation that was mid-flight when Stop was clicked).
        if (stream) {
            await _startOneRecorder();
        }
    }

    function _startRotationTimer() {
        rotationTimer = setInterval(() => {
            _rotateChunk().catch(() => {
                // A failed rotation (e.g. _startOneRecorder throwing) must not crash the
                // interval callback silently swallowing further rotations -- surfacing it as
                // recordingInterrupted lets the caller warn the doctor, same as any other
                // mid-recording failure, rather than the recording quietly going dark.
                recordingInterrupted = true;
            });
        }, CHUNK_INTERVAL_MS);
    }

    function _stopRotationTimer() {
        if (rotationTimer) {
            clearInterval(rotationTimer);
            rotationTimer = null;
        }
    }

    // --- shared upload path ---

    async function _uploadChunk(blob, index, isFinal) {
        const form = new FormData();
        form.append('session_id', sessionId);
        form.append('chunk_index', String(index));
        form.append('is_final', isFinal ? 'true' : 'false');
        form.append('audio', blob, `chunk_${index}.${extForMimeType(blob.type)}`);

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
            return fetch(`${apiBase}/transcribe-audio-chunk`, { method: 'POST', headers, body: form });
        };

        let res = await doUpload();
        // The access token is short-lived by design (15 min server-side) -- a recording that
        // runs longer than that (exactly the long-consultation case this whole pipeline exists
        // for) would otherwise ALWAYS fail here with 401 right as a chunk uploads. Verified live
        // (pre-chunking) that a 20-minute recording's single upload hit exactly this. Every
        // other authenticated call in this app (Api.get/post/upload, frontend/js/api.js)
        // already retries once after a token refresh on 401 -- this mirrors that same pattern
        // (reusing api.js's shared _refreshAccessToken(), a plain global function since both
        // files load as classic scripts in the same page) rather than inventing a second one.
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
        return res.json();
    }

    // --- public interface ---

    async function start() {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        sessionMimeType = pickMimeType();
        sessionId = _newSessionId();
        nextChunkIndex = 0;
        pendingChunkUploads = [];
        recordingInterrupted = false;
        sessionPartial = false;
        _setUpLevelMeter();
        try {
            await _startOneRecorder();
            _startRotationTimer();
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
        _stopRotationTimer();
        const finalBlob = await _flushCurrentRecorder();
        if (stream) {
            stream.getTracks().forEach((t) => t.stop());
            stream = null;
        }
        // Wait for every still-in-flight mid-recording chunk upload to have at least been SENT
        // before sending the final chunk -- avoids the final (is_final=true) upload's request
        // reaching the server before an earlier chunk's does, which would still resolve
        // correctly server-side (chunks are joined by explicit index, not arrival order) but
        // needlessly risks a race on the server's per-session dict for no benefit.
        await Promise.allSettled(pendingChunkUploads);
        pendingChunkUploads = [];

        const finalIndex = nextChunkIndex++;
        // Even a recording with nothing in its final blob (e.g. Stop clicked right at a
        // rotation boundary) still uploads an is_final marker so the server knows to finalize
        // and return whatever it already has from earlier chunks -- a session with genuinely
        // zero chunks ever (mic never captured anything at all) is the one true empty-transcript
        // case, matching the previous behavior's `if (chunks.length === 0) resolve('')`.
        if (!finalBlob && finalIndex === 0) {
            return '';
        }
        const uploadBlob = finalBlob || new Blob([], { type: sessionMimeType || 'audio/webm' });
        const data = await _uploadChunk(uploadBlob, finalIndex, true);
        sessionPartial = !!data.transcriptPartial;
        return data.transcript || '';
    }

    // True if the recording ended early on its own (device disconnect, encoder failure) rather
    // than because the caller invoked stop() on a still-healthy recording. Meaningful only
    // after stop() has resolved -- callers that care should warn the transcript may be
    // incomplete rather than presenting it as a normal, complete recording.
    function wasInterrupted() {
        return recordingInterrupted;
    }

    // True if stop() resolved normally but at least one mid-recording chunk failed to
    // transcribe server-side (a Groq/Sarvam error on that specific chunk, not a recording-level
    // problem) -- distinct from wasInterrupted(). The returned transcript is real but may be
    // missing a slice of the conversation.
    function wasPartial() {
        return sessionPartial;
    }

    return { start, stop, isSupported, getLevel, wasInterrupted, wasPartial };
}
