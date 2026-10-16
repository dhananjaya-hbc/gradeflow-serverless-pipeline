import { describe, expect, it, vi } from "vitest";
import type { UploadForm } from "../api/types";
import { MAX_BYTES, UploadError, checkFile, describeS3Error, uploadToS3 } from "./upload";

describe("checkFile", () => {
  it("accepts csv and json, in any letter case", () => {
    expect(checkFile({ name: "a.csv", size: 10 })).toBeNull();
    expect(checkFile({ name: "A.JSON", size: 10 })).toBeNull();
  });
  it("rejects other types", () => {
    expect(checkFile({ name: "a.txt", size: 10 })).toMatch(/Only .csv and .json/);
    expect(checkFile({ name: "csv", size: 10 })).toMatch(/Only .csv and .json/);
    expect(checkFile({ name: "a.csv.exe", size: 10 })).toMatch(/Only .csv and .json/);
  });
  it("rejects empty files and files over the limit, but allows exactly the limit", () => {
    expect(checkFile({ name: "a.csv", size: 0 })).toMatch(/empty/);
    expect(checkFile({ name: "a.csv", size: MAX_BYTES })).toBeNull();
    expect(checkFile({ name: "a.csv", size: MAX_BYTES + 1 })).toBe("This file is 50.1 MB, but the limit is 50 MB.");
  });
});

describe("describeS3Error", () => {
  const xml = (code: string) => `<?xml version="1.0"?><Error><Code>${code}</Code><Message>x</Message></Error>`;
  it("explains the errors S3 really returns", () => {
    expect(describeS3Error(400, xml("EntityTooSmall"))).toMatch(/empty/);
    expect(describeS3Error(400, xml("EntityTooLarge"))).toMatch(/larger than 50 MB/);
    expect(describeS3Error(403, xml("AccessDenied"))).toMatch(/expired/);
  });
  it("keeps the code for unknown errors and handles a missing body", () => {
    expect(describeS3Error(500, xml("InternalError"))).toBe("S3 rejected the upload (HTTP 500, InternalError).");
    expect(describeS3Error(502, "")).toBe("S3 rejected the upload (HTTP 502).");
  });
  it("blames network or CORS when there was no response", () => {
    expect(describeS3Error(0, "")).toMatch(/CORS/);
  });
});

const form: UploadForm = {
  dataset_id: "ds-1",
  key: "uploads/ds-1/a.csv",
  upload: { method: "POST", url: "https://bucket.s3.ap-south-1.amazonaws.com/", fields: { key: "k", policy: "p" } },
  max_bytes: MAX_BYTES,
  expires_in: 600,
};

/** A hand-made XMLHttpRequest that records what was sent. */
function fakeXhr() {
  const sent: { method?: string; url?: string; body?: FormData } = {};
  const xhr = {
    upload: {} as { onprogress?: (e: { lengthComputable: boolean; loaded: number; total: number }) => void },
    status: 0,
    responseText: "",
    open: vi.fn((method: string, url: string) => Object.assign(sent, { method, url })),
    send: vi.fn((body: FormData) => Object.assign(sent, { body })),
    abort: vi.fn(() => xhr.onabort?.(new ProgressEvent("abort"))),
  } as unknown as XMLHttpRequest & { upload: { onprogress?: (e: unknown) => void } };
  return { xhr, sent };
}

describe("uploadToS3", () => {
  const file = new File(["a,b\n1,2\n"], "a.csv", { type: "text/csv" });

  it("posts the signed fields first and the file LAST, and reports progress", async () => {
    const { xhr, sent } = fakeXhr();
    const progress: number[] = [];
    const promise = uploadToS3(form, file, { createXhr: () => xhr, onProgress: (f) => progress.push(f) });
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 });
    Object.assign(xhr, { status: 204 });
    xhr.onload?.(new ProgressEvent("load"));
    await expect(promise).resolves.toBeUndefined();

    expect(sent.method).toBe("POST");
    expect(sent.url).toBe(form.upload.url);
    expect([...sent.body!.keys()]).toEqual(["key", "policy", "file"]);
    expect(progress).toEqual([0.5, 1]);
  });

  it("turns an S3 error response into a readable UploadError", async () => {
    const { xhr } = fakeXhr();
    const promise = uploadToS3(form, file, { createXhr: () => xhr });
    Object.assign(xhr, { status: 400, responseText: "<Error><Code>EntityTooSmall</Code></Error>" });
    xhr.onload?.(new ProgressEvent("load"));
    await expect(promise).rejects.toBeInstanceOf(UploadError);
    await expect(promise).rejects.toThrow(/empty/);
  });

  it("reports a network failure", async () => {
    const { xhr } = fakeXhr();
    const promise = uploadToS3(form, file, { createXhr: () => xhr });
    xhr.onerror?.(new ProgressEvent("error"));
    await expect(promise).rejects.toThrow(/Could not reach S3/);
  });

  it("aborts when the signal fires, and never starts if already aborted", async () => {
    const { xhr } = fakeXhr();
    const controller = new AbortController();
    const promise = uploadToS3(form, file, { createXhr: () => xhr, signal: controller.signal });
    controller.abort();
    await expect(promise).rejects.toMatchObject({ name: "AbortError" });
    expect(xhr.abort).toHaveBeenCalled();

    const second = fakeXhr();
    await expect(uploadToS3(form, file, { createXhr: () => second.xhr, signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
    expect(second.xhr.open).not.toHaveBeenCalled();
  });
});
