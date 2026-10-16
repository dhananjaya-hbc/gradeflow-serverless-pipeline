import type { UploadForm } from "../api/types";

export const MAX_BYTES = 50 * 1024 * 1024; // the API signs the same limit; S3 enforces it
const ALLOWED = /\.(csv|json)$/i;

/** A reason the file cannot be uploaded, or null if it looks fine. S3 re-checks the size. */
export function checkFile(file: { name: string; size: number }, maxBytes = MAX_BYTES): string | null {
  if (!ALLOWED.test(file.name)) return "Only .csv and .json files can be uploaded.";
  if (file.size === 0) return "This file is empty.";
  if (file.size > maxBytes) {
    return `This file is ${formatMb(file.size, "up")}, but the limit is ${formatMb(maxBytes)}.`;
  }
  return null;
}

/** "up" rounds up, so a file just over the limit never reads as equal to it. */
export function formatMb(bytes: number, round: "nearest" | "up" = "nearest"): string {
  const tenths = (bytes / (1024 * 1024)) * 10;
  return `${(round === "up" ? Math.ceil(tenths) : Math.round(tenths)) / 10} MB`;
}

/** A readable message for a failed S3 upload. S3 answers errors as XML with a <Code>. */
export function describeS3Error(status: number, body: string): string {
  if (status === 0) {
    return "Could not reach S3. Check your connection; if it keeps failing, the bucket's CORS rule may not allow this site.";
  }
  const code = /<Code>([^<]+)<\/Code>/.exec(body)?.[1];
  switch (code) {
    case "EntityTooSmall":
      return "S3 rejected the file because it is empty.";
    case "EntityTooLarge":
      return `S3 rejected the file because it is larger than ${formatMb(MAX_BYTES)}.`;
    case "AccessDenied":
    case "ExpiredToken":
    case "RequestExpired":
      return "The upload link has expired or is not allowed. Start the upload again.";
    default:
      return `S3 rejected the upload (HTTP ${status}${code ? `, ${code}` : ""}).`;
  }
}

export class UploadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "UploadError";
  }
}

export interface UploadOptions {
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
  /** Injectable for tests. */
  createXhr?: () => XMLHttpRequest;
}

/**
 * POST the file to S3 with the pre-signed form. XMLHttpRequest is used because `fetch` cannot
 * report upload progress. S3 requires the `file` field to come LAST.
 */
export function uploadToS3(form: UploadForm, file: File, options: UploadOptions = {}): Promise<void> {
  const { onProgress, signal, createXhr = () => new XMLHttpRequest() } = options;
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Upload cancelled", "AbortError"));
    const xhr = createXhr();
    const body = new FormData();
    for (const [name, value] of Object.entries(form.upload.fields)) body.append(name, value);
    body.append("file", file);

    const onAbort = () => xhr.abort();
    signal?.addEventListener("abort", onAbort);
    const done = () => signal?.removeEventListener("abort", onAbort);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => {
      done();
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(1);
        resolve();
      } else {
        reject(new UploadError(describeS3Error(xhr.status, xhr.responseText ?? "")));
      }
    };
    xhr.onerror = () => {
      done();
      reject(new UploadError(describeS3Error(0, "")));
    };
    xhr.onabort = () => {
      done();
      reject(new DOMException("Upload cancelled", "AbortError"));
    };
    xhr.open(form.upload.method, form.upload.url);
    xhr.send(body);
  });
}
