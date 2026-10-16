import { useRef, useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api/context";
import { ErrorBox } from "../components/ErrorBox";
import { useUpload } from "../hooks/useUpload";
import { MAX_BYTES, formatMb } from "../lib/upload";

export function UploadPage() {
  const { state, start, cancel, reset } = useUpload(useApi());
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const busy = state.phase === "requesting" || state.phase === "uploading" || state.phase === "waiting";

  function pick(files: FileList | null) {
    const file = files?.[0];
    if (file) void start(file);
    if (input.current) input.current.value = ""; // allow choosing the same file again
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (!busy) pick(event.dataTransfer.files);
  }

  return (
    <section>
      <h1>Upload a dataset</h1>
      <p>
        Choose a CSV or JSON file of student results (up to {formatMb(MAX_BYTES)}). It is processed automatically.
      </p>

      <div
        className={`dropzone${dragging ? " dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <label htmlFor="file">Drop a file here, or choose one:</label>{" "}
        <input
          id="file"
          ref={input}
          type="file"
          accept=".csv,.json,text/csv,application/json"
          disabled={busy}
          onChange={(event) => pick(event.target.files)}
        />
      </div>

      {state.phase === "requesting" && <p role="status">Preparing the upload…</p>}
      {state.phase === "uploading" && (
        <p role="status">
          <label htmlFor="progress">Uploading… {Math.round(state.progress * 100)}%</label>{" "}
          <progress id="progress" max={1} value={state.progress} />
        </p>
      )}
      {state.phase === "waiting" && <p role="status">Uploaded. Waiting for processing to start…</p>}
      {busy && (
        <button type="button" onClick={cancel}>
          Cancel
        </button>
      )}

      {state.phase === "done" && (
        <div className="success-box" role="status">
          <strong>Upload complete.</strong>
          {state.registered ? (
            <p>The dataset is being processed.</p>
          ) : (
            <p>Processing has not started yet. It should appear in the list shortly.</p>
          )}
          <p>
            <Link to="/">Go to datasets</Link> · <button type="button" onClick={reset}>Upload another</button>
          </p>
        </div>
      )}
      {state.phase === "error" && <ErrorBox title="Upload failed" message={state.message} onRetry={reset} />}
    </section>
  );
}
