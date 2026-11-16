const recordings = document.getElementById("recordings");
const recordingList = document.getElementById("recordingList");
const recordingStatus = document.getElementById("recordingStatus");
const deleteRecordingsButton = document.getElementById("deleteRecordings");
const objectUrls = [];

function renderRecordingGroups(clips) {
    const groups = new Map();
    for (const clip of clips) {
        const group = groups.get(clip.groupId) || [];
        group.push(clip);
        groups.set(clip.groupId, group);
    }

    for (const group of groups.values()) {
        group.sort((left, right) => left.chunkIndex - right.chunkIndex);
        const firstClip = group[0];
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        const answerAudio = firstClip.kind === "answer-audio";
        summary.textContent = `${answerAudio ? "Answer audio · " : ""}Question ${firstClip.position} · ${new Date(firstClip.createdAt).toLocaleString()}`;
        const player = document.createElement(answerAudio ? "audio" : "video");
        player.controls = true;
        player.preload = "none";
        if (!answerAudio) {
            player.width = 480;
        }
        const blob = new Blob(group.map(clip => clip.blob), {type: firstClip.mimeType});
        const objectUrl = URL.createObjectURL(blob);
        objectUrls.push(objectUrl);
        player.src = objectUrl;
        details.append(summary, player);
        recordingList.append(details);
    }
}

async function loadRecordings() {
    try {
        const clips = await window.localInterviewRecordings.listForInterview(
            recordings.dataset.owner,
            recordings.dataset.interviewId
        );
        if (!clips.length) {
            recordingStatus.textContent = "No local recording clips were found for this interview.";
            return;
        }
        recordingStatus.textContent = "Recordings are stored only in this browser.";
        renderRecordingGroups(clips);
    } catch (error) {
        recordingStatus.textContent = `Could not load local recordings: ${error.message}`;
    }
}

deleteRecordingsButton.addEventListener("click", async () => {
    if (!window.confirm("Delete all local recordings for this account from this browser? This cannot be undone.")) {
        return;
    }
    deleteRecordingsButton.disabled = true;
    try {
        await window.localInterviewRecordings.deleteInterview(
            recordings.dataset.owner,
            recordings.dataset.interviewId
        );
        recordingList.replaceChildren();
        recordingStatus.textContent = "Local recordings for this interview were deleted.";
        objectUrls.forEach(objectUrl => URL.revokeObjectURL(objectUrl));
        objectUrls.length = 0;
    } catch (error) {
        recordingStatus.textContent = `Could not delete local recordings: ${error.message}`;
    } finally {
        deleteRecordingsButton.disabled = false;
    }
});

window.addEventListener("pagehide", () => {
    objectUrls.forEach(objectUrl => URL.revokeObjectURL(objectUrl));
});

void loadRecordings();
