const page = document.body;
const interviewId = page.dataset.interviewId;
const questionId = page.dataset.questionId;
const questionPosition = Number(page.dataset.questionPosition);
const recordingEnabled = page.dataset.recordingEnabled === "true";
const answerForm = document.getElementById("answerForm");
const answerInput = document.getElementById("answer");
const submitButton = document.getElementById("submitAnswer");
const skipButton = document.getElementById("skipQuestion");
const answerAudioButton = document.getElementById("answerAudioButton");
const answerAudioStatus = document.getElementById("answerAudioStatus");
const answerAudioPreview = document.getElementById("answerAudioPreview");
const microphoneSelect = document.getElementById("microphoneSelect");
const microphoneStatus = document.getElementById("microphoneStatus");
const answerAudioLevel = document.getElementById("answerAudioLevel");
const answerAudioLevelStatus = document.getElementById("answerAudioLevelStatus");
const statusElement = document.getElementById("status");
const integrityWarning = document.getElementById("integrityWarning");
const cameraElement = document.getElementById("cameraFeed");
const cameraHint = document.getElementById("cameraHint");
const recordingButton = document.getElementById("recordingButton");
const recordingStatus = document.getElementById("recordingStatus");
const sessionTimer = document.getElementById("sessionTimer");

let isBusy = false;
let mediaStream;
let mediaRecorder;
let recordingWrites = [];
let recordingError;
let isFinishing = false;
let integrityWarningCount = 0;
let interviewTabHidden = false;
const interviewTitle = document.title;
let answerAudioStream;
let ownsAnswerAudioStream = false;
let answerAudioRecorder;
let answerAudioChunks = [];
let answerAudioSavePromise;
let answerAudioObjectUrl;
let answerAudioError;
let isSavingAnswerAudio = false;
let isStartingAnswerAudio = false;
let answerAudioContext;
let answerAudioAnalyser;
let answerAudioMeterFrame;

function setBusy(busy) {
    isBusy = busy;
    updateAnswerAudioControls();
}

function showError(message) {
    statusElement.textContent = message;
}

function showIntegrityWarning(message) {
    integrityWarningCount += 1;
    integrityWarning.hidden = false;
    integrityWarning.textContent = `Warning ${integrityWarningCount}: ${message}`;
}

function blockClipboardAction(event) {
    event.preventDefault();
    const messages = {
        copy: "Copying is disabled during the interview.",
        cut: "Cutting text is disabled during the interview.",
        paste: "Pasting is disabled during the interview. Type your own answer."
    };
    showIntegrityWarning(messages[event.type]);
}

function updateAnswerAudioControls() {
    const isRecording = answerAudioRecorder?.state === "recording";
    answerAudioButton.setAttribute("aria-pressed", String(isRecording));
    answerAudioButton.setAttribute(
        "aria-label",
        isRecording ? "Stop and save audio recording" : "Record audio answer"
    );
    answerAudioButton.title = isRecording ? "Stop and save audio recording" : "Record audio answer";
    answerAudioButton.disabled = isBusy || isStartingAnswerAudio || isSavingAnswerAudio;
    microphoneSelect.disabled = isBusy || isStartingAnswerAudio || isRecording || isSavingAnswerAudio;
    submitButton.disabled = isBusy || isStartingAnswerAudio || isRecording || isSavingAnswerAudio;
    skipButton.disabled = isBusy || isStartingAnswerAudio || isRecording || isSavingAnswerAudio;
}

async function refreshMicrophoneDevices() {
    if (!navigator.mediaDevices?.enumerateDevices) {
        microphoneStatus.textContent = "This browser cannot list microphone inputs.";
        microphoneSelect.disabled = true;
        return;
    }
    try {
        const selectedDeviceId = microphoneSelect.value;
        const devices = (await navigator.mediaDevices.enumerateDevices())
            .filter(device => device.kind === "audioinput");
        const options = [new Option("System default microphone", "")];
        devices.forEach((device, index) => {
            options.push(new Option(device.label || `Microphone ${index + 1}`, device.deviceId));
        });
        microphoneSelect.replaceChildren(...options);
        microphoneSelect.value = devices.some(device => device.deviceId === selectedDeviceId)
            ? selectedDeviceId
            : "";
        microphoneStatus.textContent = devices.length
            ? "Choose the input you speak into; selection applies to the next recording."
            : "No microphone inputs were found.";
    } catch (error) {
        microphoneStatus.textContent = `Microphone inputs could not be listed: ${error.message}`;
    }
}

function stopAnswerAudioMeter() {
    if (answerAudioMeterFrame !== undefined) {
        window.cancelAnimationFrame(answerAudioMeterFrame);
        answerAudioMeterFrame = undefined;
    }
    answerAudioAnalyser = undefined;
    if (answerAudioContext && answerAudioContext.state !== "closed") {
        void answerAudioContext.close();
    }
    answerAudioContext = undefined;
    answerAudioLevel.hidden = true;
    answerAudioLevel.value = 0;
}

function releaseAnswerAudioStream() {
    stopAnswerAudioMeter();
    if (ownsAnswerAudioStream) {
        answerAudioStream?.getAudioTracks().forEach(track => track.stop());
    }
    answerAudioStream = undefined;
    ownsAnswerAudioStream = false;
}

async function monitorAnswerAudioLevel(stream) {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) {
        answerAudioLevelStatus.textContent = "Live microphone level is unavailable in this browser; recording can continue.";
        return;
    }
    try {
        answerAudioContext = new AudioContext();
        await answerAudioContext.resume();
        if (answerAudioRecorder?.state !== "recording") {
            stopAnswerAudioMeter();
            return;
        }
        answerAudioAnalyser = answerAudioContext.createAnalyser();
        answerAudioAnalyser.fftSize = 1024;
        answerAudioContext.createMediaStreamSource(stream).connect(answerAudioAnalyser);
        answerAudioLevel.hidden = false;
        answerAudioLevelStatus.textContent = "Speak now and check that the microphone level moves.";
        const samples = new Uint8Array(answerAudioAnalyser.fftSize);
        const updateLevel = () => {
            if (!answerAudioAnalyser) {
                return;
            }
            answerAudioAnalyser.getByteTimeDomainData(samples);
            let squareTotal = 0;
            for (const sample of samples) {
                const normalized = (sample - 128) / 128;
                squareTotal += normalized * normalized;
            }
            const level = Math.sqrt(squareTotal / samples.length);
            answerAudioLevel.value = Math.min(1, level * 20);
            answerAudioLevelStatus.textContent = level > 0.004
                ? "Microphone is picking up sound."
                : "No sound detected yet. Check the selected microphone and its mute switch.";
            answerAudioMeterFrame = window.requestAnimationFrame(updateLevel);
        };
        updateLevel();
    } catch (error) {
        stopAnswerAudioMeter();
        answerAudioLevelStatus.textContent = `Microphone level check failed: ${error.message}. Recording can continue.`;
    }
}

async function startAnswerAudioRecording() {
    if (isStartingAnswerAudio) {
        return;
    }
    if (!window.localInterviewRecordings) {
        answerAudioStatus.textContent = "Local recording storage is unavailable in this browser.";
        return;
    }
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) {
        answerAudioStatus.textContent = "Audio recording is not supported in this browser.";
        return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
        answerAudioStatus.textContent = "Microphone access is not supported in this browser.";
        return;
    }

    isStartingAnswerAudio = true;
    answerAudioStatus.textContent = "Requesting microphone access...";
    answerAudioLevelStatus.textContent = "";
    updateAnswerAudioControls();
    try {
        const selectedDeviceId = microphoneSelect.value;
        answerAudioStream = await navigator.mediaDevices.getUserMedia({
            audio: selectedDeviceId ? {deviceId: {exact: selectedDeviceId}} : true
        });
        ownsAnswerAudioStream = true;
        if (!answerAudioStream.getAudioTracks().some(track => track.readyState === "live")) {
            throw new Error("The browser did not provide an active microphone track.");
        }
        await refreshMicrophoneDevices();
        const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"]
            .find(type => MediaRecorder.isTypeSupported(type));
        answerAudioRecorder = new MediaRecorder(
            answerAudioStream,
            mimeType ? {mimeType} : undefined
        );
        answerAudioChunks = [];
        answerAudioError = undefined;
        answerAudioSavePromise = undefined;
        answerAudioRecorder.ondataavailable = event => {
            if (event.data.size) {
                answerAudioChunks.push(event.data);
            }
        };
        answerAudioRecorder.onerror = event => {
            answerAudioError = event.error || new Error("The browser stopped recording unexpectedly.");
            answerAudioStatus.textContent = `Audio recording error: ${answerAudioError.message}`;
            if (answerAudioRecorder?.state === "recording") {
                void stopAnswerAudioRecording().catch(() => {});
            } else {
                releaseAnswerAudioStream();
                answerAudioRecorder = undefined;
                answerAudioChunks = [];
                updateAnswerAudioControls();
            }
        };
        answerAudioRecorder.onstart = () => {
            answerAudioStatus.textContent = "Recording audio. Speak now, then click the red microphone to stop and save locally.";
            void monitorAnswerAudioLevel(answerAudioStream);
        };
        answerAudioRecorder.start(1000);
        answerAudioStatus.textContent = "Starting microphone recording...";
    } catch (error) {
        answerAudioRecorder = undefined;
        releaseAnswerAudioStream();
        answerAudioLevelStatus.textContent = "";
        answerAudioStatus.textContent = error.name === "NotAllowedError"
            ? "Microphone access was denied. Allow microphone access for this site, then try again."
            : error.name === "NotFoundError"
                ? "No microphone is available to the browser."
                : error.name === "OverconstrainedError"
                    ? "The selected microphone is unavailable. Choose System default microphone or another input."
                    : `Audio recording could not start: ${error.message}`;
    } finally {
        isStartingAnswerAudio = false;
        updateAnswerAudioControls();
    }
}

function stopAnswerAudioRecording() {
    const recorder = answerAudioRecorder;
    if (answerAudioSavePromise) {
        return answerAudioSavePromise;
    }
    if (!recorder || recorder.state === "inactive") {
        return Promise.resolve();
    }

    isSavingAnswerAudio = true;
    answerAudioStatus.textContent = "Saving your audio in this browser...";
    updateAnswerAudioControls();
    answerAudioSavePromise = new Promise((resolve, reject) => {
        recorder.addEventListener("stop", async () => {
            try {
                if (answerAudioError) {
                    throw answerAudioError;
                }
                const blob = new Blob(answerAudioChunks, {
                    type: recorder.mimeType || "audio/webm"
                });
                if (!blob.size) {
                    throw new Error("The recording is empty. Check your microphone and try again.");
                }
                await window.localInterviewRecordings.saveClip({
                    owner: page.dataset.username,
                    interviewId: Number(interviewId),
                    questionId: Number(questionId),
                    position: questionPosition,
                    groupId: `answer-${questionId}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
                    chunkIndex: 0,
                    mimeType: blob.type || "audio/webm",
                    kind: "answer-audio",
                    blob
                });
                if (answerAudioObjectUrl) {
                    URL.revokeObjectURL(answerAudioObjectUrl);
                }
                answerAudioObjectUrl = URL.createObjectURL(blob);
                answerAudioPreview.src = answerAudioObjectUrl;
                answerAudioPreview.hidden = false;
                answerAudioStatus.textContent = "Audio saved only in this browser. Listen below, then type or edit the transcript; feedback uses that text.";
                resolve();
            } catch (error) {
                answerAudioStatus.textContent = `Audio could not be saved: ${error.message}`;
                reject(error);
            } finally {
                releaseAnswerAudioStream();
                answerAudioRecorder = undefined;
                answerAudioChunks = [];
                answerAudioError = undefined;
                isSavingAnswerAudio = false;
                answerAudioSavePromise = undefined;
                updateAnswerAudioControls();
            }
        }, {once: true});
    });
    try {
        recorder.stop();
    } catch (error) {
        releaseAnswerAudioStream();
        answerAudioRecorder = undefined;
        answerAudioChunks = [];
        isSavingAnswerAudio = false;
        answerAudioSavePromise = undefined;
        answerAudioStatus.textContent = `Audio could not be saved: ${error.message}`;
        updateAnswerAudioControls();
        return Promise.reject(error);
    }
    return answerAudioSavePromise;
}

function stopRecordingAndSave() {
    if (!mediaRecorder || mediaRecorder.state === "inactive") {
        return Promise.resolve();
    }
    const recorder = mediaRecorder;
    return new Promise((resolve, reject) => {
        recorder.addEventListener("stop", async () => {
            await Promise.all(recordingWrites);
            recordingWrites = [];
            if (recordingError) {
                const error = recordingError;
                recordingError = undefined;
                reject(error);
                return;
            }
            resolve();
        }, {once: true});
        recorder.stop();
    });
}

function startRecording() {
    if (!recordingEnabled || !mediaStream || !window.localInterviewRecordings) {
        return;
    }
    const unavailable = message => {
        recordingButton.textContent = "Recording unavailable";
        recordingButton.setAttribute("aria-pressed", "false");
        recordingButton.disabled = true;
        recordingStatus.textContent = message;
    };
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) {
        unavailable("This browser does not support local video recording. The interview can continue.");
        return;
    }
    if (!mediaStream.getAudioTracks().length) {
        unavailable("Microphone access is needed for video and audio recording. The interview can continue.");
        return;
    }

    const mimeType = ["video/webm;codecs=vp8,opus", "video/webm"]
        .find(type => MediaRecorder.isTypeSupported(type));
    let recorder;
    try {
        recorder = new MediaRecorder(
            mediaStream,
            {
                ...(mimeType ? {mimeType} : {}),
                videoBitsPerSecond: 400_000,
                audioBitsPerSecond: 64_000
            }
        );
    } catch (error) {
        unavailable(`Recording could not start: ${error.message}. The interview can continue.`);
        return;
    }

    mediaRecorder = recorder;
    recordingWrites = [];
    recordingError = undefined;
    let chunkIndex = 0;
    const groupId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    recorder.ondataavailable = event => {
        if (!event.data.size) {
            return;
        }
        const write = window.localInterviewRecordings.saveClip({
            owner: page.dataset.username,
            interviewId: Number(interviewId),
            questionId: Number(questionId),
            position: questionPosition,
            groupId,
            chunkIndex: chunkIndex++,
            mimeType: recorder.mimeType || "video/webm",
            blob: event.data
        }).catch(error => {
            recordingError = error;
            recordingStatus.textContent = `A recording segment could not be saved: ${error.message}`;
        });
        recordingWrites.push(write);
    };
    recorder.onerror = event => {
        recordingError = event.error || new Error("The browser stopped recording unexpectedly.");
        recordingStatus.textContent = `Recording error: ${recordingError.message}`;
    };
    try {
        recorder.start(5000);
    } catch (error) {
        mediaRecorder = undefined;
        unavailable(`Recording could not start: ${error.message}. The interview can continue.`);
        return;
    }
    recordingButton.textContent = "Stop recording";
    recordingButton.setAttribute("aria-pressed", "true");
    recordingStatus.textContent = "Recording video and audio to this browser only.";
}

async function initializeCamera() {
    if (!navigator.mediaDevices?.getUserMedia) {
        cameraHint.textContent = "Camera preview requires a browser that supports local camera access.";
        if (recordingEnabled) {
            recordingStatus.textContent = "Camera and microphone access are unavailable in this browser.";
        }
        return;
    }
    try {
        mediaStream = await navigator.mediaDevices.getUserMedia({
            video: {width: {ideal: 640}, height: {ideal: 480}},
            audio: recordingEnabled
        });
        cameraElement.srcObject = mediaStream;
        await cameraElement.play();
        cameraHint.textContent = "Practice with your camera on to build confidence delivering answers.";
        if (recordingEnabled) {
            startRecording();
        }
    } catch (error) {
        cameraHint.textContent = "Camera access was not available. Check browser permissions and your webcam connection.";
        if (recordingEnabled) {
            recordingStatus.textContent = `Recording could not start: ${error.message}. Your answers can still be saved.`;
        }
    }
}

async function persistRecordingBeforeLeaving() {
    if (!recordingEnabled || !mediaRecorder || mediaRecorder.state === "inactive") {
        return;
    }
    await stopRecordingAndSave();
    recordingButton.textContent = "Recording saved";
    recordingButton.disabled = true;
    recordingButton.setAttribute("aria-pressed", "false");
    recordingStatus.textContent = "This question's recording is saved locally in your browser.";
}

async function postForm(url, formData) {
    setBusy(true);
    try {
        try {
            await persistRecordingBeforeLeaving();
        } catch (error) {
            showError(`Recording could not be saved: ${error.message}. Your answer will still be submitted.`);
        }
        const response = await fetch(url, {method: "POST", body: formData});
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || "Could not save your response.");
        }
        window.location.assign(result.complete ? result.result_url : result.next_url);
    } catch (error) {
        if (error.message.includes("session time has expired")) {
            finishTimedInterview();
            return;
        }
        showError(error.message);
        setBusy(false);
    }
}

async function finishTimedInterview() {
    if (isFinishing) {
        return;
    }
    isFinishing = true;
    setBusy(true);
    try {
        if (answerAudioRecorder?.state === "recording" || answerAudioSavePromise) {
            try {
                await stopAnswerAudioRecording();
            } catch (error) {
                answerAudioStatus.textContent = `Audio could not be saved: ${error.message}. Finishing the interview.`;
            }
        }
        try {
            await persistRecordingBeforeLeaving();
        } catch (error) {
            showError(`Recording could not be saved: ${error.message}. Finishing the interview.`);
        }
        const response = await fetch(`/interview/${interviewId}/finish`, {method: "POST"});
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || "Could not finish the interview.");
        }
        window.location.assign(result.result_url);
    } catch (error) {
        isFinishing = false;
        showError(error.message);
        setBusy(false);
    }
}

function startSessionTimer() {
    let remaining = Number(page.dataset.remainingSeconds);
    let timer;
    const render = () => {
        const minutes = Math.floor(remaining / 60).toString().padStart(2, "0");
        const seconds = (remaining % 60).toString().padStart(2, "0");
        sessionTimer.textContent = `Time remaining: ${minutes}:${seconds}`;
        if (remaining <= 0) {
            window.clearInterval(timer);
            finishTimedInterview();
        }
        remaining -= 1;
    };
    render();
    timer = window.setInterval(render, 1000);
}

answerForm.addEventListener("submit", event => {
    event.preventDefault();
    if (isStartingAnswerAudio || answerAudioRecorder?.state === "recording" || isSavingAnswerAudio) {
        showError("Stop the audio recording and wait for it to save before submitting your transcript.");
        answerInput.focus();
        return;
    }
    const answer = answerInput.value.trim();
    if (!answer) {
        showError("Write an answer or skip this question.");
        answerInput.focus();
        return;
    }
    const formData = new FormData(answerForm);
    formData.set("question_id", questionId);
    postForm(`/interview/${interviewId}/answer`, formData);
});

document.addEventListener("copy", blockClipboardAction, true);
document.addEventListener("cut", blockClipboardAction, true);
document.addEventListener("paste", blockClipboardAction, true);
document.addEventListener("keydown", event => {
    const key = event.key.toLowerCase();
    const clipboardShortcut = (event.ctrlKey || event.metaKey) && ["c", "v", "x"].includes(key);
    const pasteShortcut = event.shiftKey && key === "insert";
    if (!clipboardShortcut && !pasteShortcut) {
        return;
    }
    event.preventDefault();
    showIntegrityWarning(
        pasteShortcut || key === "v"
            ? "Pasting is disabled during the interview. Type your own answer."
            : "Copying and cutting are disabled during the interview."
    );
}, true);

document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
        interviewTabHidden = true;
        document.title = `Return to interview - ${interviewTitle}`;
        return;
    }
    if (interviewTabHidden) {
        interviewTabHidden = false;
        document.title = interviewTitle;
        showIntegrityWarning("Switching tabs during the interview is not allowed. Stay on this page; the timer keeps running.");
    }
});

skipButton.addEventListener("click", () => {
    if (isStartingAnswerAudio || answerAudioRecorder?.state === "recording" || isSavingAnswerAudio) {
        showError("Stop the audio recording and wait for it to save before skipping.");
        return;
    }
    const formData = new FormData();
    formData.set("question_id", questionId);
    postForm(`/interview/${interviewId}/skip`, formData);
});

answerAudioButton.addEventListener("click", async () => {
    if (answerAudioRecorder?.state === "recording") {
        try {
            await stopAnswerAudioRecording();
        } catch (error) {
            showError(`Audio could not be saved: ${error.message}`);
        }
        return;
    }
    await startAnswerAudioRecording();
});

if (recordingEnabled) {
    recordingButton.addEventListener("click", async () => {
        if (mediaRecorder?.state === "recording") {
            recordingButton.disabled = true;
            recordingStatus.textContent = "Saving this recording locally...";
            try {
                await stopRecordingAndSave();
                recordingButton.textContent = "Start recording";
                recordingButton.setAttribute("aria-pressed", "false");
                recordingStatus.textContent = "Recording saved locally. Start again to record more.";
            } catch (error) {
                recordingStatus.textContent = `Recording could not be saved: ${error.message}`;
            } finally {
                recordingButton.disabled = false;
            }
        } else {
            startRecording();
        }
    });
}

window.addEventListener("pagehide", () => {
    if (answerAudioRecorder?.state === "recording") {
        try {
            answerAudioRecorder.stop();
        } catch (error) {
            answerAudioStatus.textContent = `Audio recording stopped unexpectedly: ${error.message}`;
        }
    }
    mediaStream?.getTracks().forEach(track => track.stop());
    releaseAnswerAudioStream();
    if (answerAudioObjectUrl) {
        URL.revokeObjectURL(answerAudioObjectUrl);
    }
});

window.addEventListener("beforeunload", event => {
    if (answerAudioRecorder?.state !== "recording") {
        return;
    }
    event.preventDefault();
    event.returnValue = "";
});

void initializeCamera();
startSessionTimer();
updateAnswerAudioControls();
