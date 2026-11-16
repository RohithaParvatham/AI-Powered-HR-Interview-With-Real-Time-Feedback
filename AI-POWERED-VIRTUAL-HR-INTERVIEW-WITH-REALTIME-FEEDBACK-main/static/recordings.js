(() => {
    const databaseName = "interview-practice-recordings";
    let databasePromise;

    function openDatabase() {
        if (!("indexedDB" in window)) {
            return Promise.reject(new Error("This browser cannot store local recordings."));
        }
        if (!databasePromise) {
            databasePromise = new Promise((resolve, reject) => {
                const request = indexedDB.open(databaseName, 1);
                request.onupgradeneeded = () => {
                    const database = request.result;
                    if (!database.objectStoreNames.contains("clips")) {
                        const store = database.createObjectStore("clips", {keyPath: "key"});
                        store.createIndex("byOwnerInterview", ["owner", "interviewId"]);
                    }
                };
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error || new Error("Could not open local recording storage."));
            });
        }
        return databasePromise;
    }

    async function saveClip(clip) {
        const database = await openDatabase();
        const transaction = database.transaction("clips", "readwrite");
        const store = transaction.objectStore("clips");
        const key = [
            clip.interviewId,
            clip.questionId,
            Date.now(),
            Math.random().toString(36).slice(2)
        ].join(":");
        store.put({...clip, key, createdAt: Date.now()});
        return new Promise((resolve, reject) => {
            transaction.oncomplete = () => resolve();
            transaction.onerror = () => reject(transaction.error || new Error("Could not save this recording."));
            transaction.onabort = () => reject(transaction.error || new Error("Saving this recording was canceled."));
        });
    }

    async function listForInterview(owner, interviewId) {
        const database = await openDatabase();
        const transaction = database.transaction("clips", "readonly");
        const request = transaction.objectStore("clips")
            .index("byOwnerInterview")
            .getAll([owner, Number(interviewId)]);
        return new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(
                request.result.sort((left, right) =>
                    left.position - right.position || left.createdAt - right.createdAt
                )
            );
            request.onerror = () => reject(request.error || new Error("Could not read local recordings."));
        });
    }

    async function deleteInterview(owner, interviewId) {
        const database = await openDatabase();
        const transaction = database.transaction("clips", "readwrite");
        const store = transaction.objectStore("clips");
        const request = store.index("byOwnerInterview").openCursor([owner, Number(interviewId)]);
        request.onsuccess = () => {
            const cursor = request.result;
            if (cursor) {
                cursor.delete();
                cursor.continue();
            }
        };
        return new Promise((resolve, reject) => {
            transaction.oncomplete = () => resolve();
            transaction.onerror = () => reject(transaction.error || new Error("Could not delete local recordings."));
            transaction.onabort = () => reject(transaction.error || new Error("Deleting local recordings was canceled."));
        });
    }

    async function deleteAll(owner) {
        const database = await openDatabase();
        const transaction = database.transaction("clips", "readwrite");
        const store = transaction.objectStore("clips");
        const request = store.index("byOwnerInterview").openCursor(IDBKeyRange.bound([owner, 0], [owner, Number.MAX_SAFE_INTEGER]));
        request.onsuccess = () => {
            const cursor = request.result;
            if (cursor) {
                cursor.delete();
                cursor.continue();
            }
        };
        return new Promise((resolve, reject) => {
            transaction.oncomplete = () => resolve();
            transaction.onerror = () => reject(transaction.error || new Error("Could not delete local recordings."));
            transaction.onabort = () => reject(transaction.error || new Error("Deleting local recordings was canceled."));
        });
    }

    window.localInterviewRecordings = Object.freeze({
        saveClip,
        listForInterview,
        deleteInterview,
        deleteAll
    });
})();
