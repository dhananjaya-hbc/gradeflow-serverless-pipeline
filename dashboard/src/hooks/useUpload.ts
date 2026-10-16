import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import { checkFile, uploadToS3, type UploadOptions } from "../lib/upload";
import type { UploadForm } from "../api/types";

export type UploadState =
  | { phase: "idle" }
  | { phase: "requesting" } // asking the API for a signed form
  | { phase: "uploading"; progress: number } // sending the file to S3, 0..1
  | { phase: "waiting" } // uploaded, waiting for the pipeline to register the dataset
  | { phase: "done"; datasetId: string; registered: boolean }
  | { phase: "error"; message: string };

export interface UploadDeps {
  upload: (form: UploadForm, file: File, options: UploadOptions) => Promise<void>;
  sleep: (ms: number) => Promise<void>;
}

const defaultDeps: UploadDeps = {
  upload: uploadToS3,
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
};

const REGISTER_POLL_MS = 2_000;
const REGISTER_TRIES = 15; // about 30 s: the Trigger normally registers a dataset within seconds

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** The upload flow: signed form from the API, file to S3, then wait until the dataset exists. */
export function useUpload(client: ApiClient, deps: UploadDeps = defaultDeps) {
  const [state, setState] = useState<UploadState>({ phase: "idle" });
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []);

  const start = useCallback(
    async (file: File) => {
      const problem = checkFile(file);
      if (problem) return setState({ phase: "error", message: problem });

      controller.current?.abort();
      const own = new AbortController();
      controller.current = own;
      const { signal } = own;
      try {
        setState({ phase: "requesting" });
        const form = await client.createUpload(file.name, signal);
        setState({ phase: "uploading", progress: 0 });
        await deps.upload(form, file, {
          signal,
          onProgress: (progress) => setState({ phase: "uploading", progress }),
        });

        setState({ phase: "waiting" });
        let registered = false;
        for (let attempt = 0; attempt < REGISTER_TRIES && !registered; attempt += 1) {
          try {
            await client.getDataset(form.dataset_id, signal);
            registered = true;
          } catch (error) {
            if (isAbort(error)) throw error;
            // 404 means "not registered yet"; the file is already uploaded, so no failure here
            // is fatal: keep trying, then report "not registered yet" instead of an error
            if (attempt < REGISTER_TRIES - 1) await deps.sleep(REGISTER_POLL_MS);
          }
        }
        setState({ phase: "done", datasetId: form.dataset_id, registered });
      } catch (error) {
        if (isAbort(error)) return setState({ phase: "idle" });
        setState({ phase: "error", message: error instanceof Error ? error.message : String(error) });
      }
    },
    [client, deps],
  );

  const cancel = useCallback(() => controller.current?.abort(), []);
  const reset = useCallback(() => setState({ phase: "idle" }), []);

  return { state, start, cancel, reset };
}
