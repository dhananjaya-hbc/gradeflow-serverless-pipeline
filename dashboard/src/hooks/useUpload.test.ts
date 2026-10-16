import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type ApiClient } from "../api/client";
import type { UploadForm } from "../api/types";
import { deferred } from "../test/helpers";
import { UploadError } from "../lib/upload";
import { useUpload, type UploadDeps } from "./useUpload";

const form: UploadForm = {
  dataset_id: "ds-1",
  key: "uploads/ds-1/a.csv",
  upload: { method: "POST", url: "https://s3.example/", fields: { key: "k" } },
  max_bytes: 50 * 1024 * 1024,
  expires_in: 600,
};
const file = new File(["a,b\n1,2\n"], "a.csv");
const notFound = () => new ApiError(404, "not_found", "no such dataset");

function setup(client: Partial<ApiClient>, deps: Partial<UploadDeps> = {}) {
  const fullDeps: UploadDeps = { upload: vi.fn().mockResolvedValue(undefined), sleep: vi.fn().mockResolvedValue(undefined), ...deps };
  const hook = renderHook(() => useUpload(client as ApiClient, fullDeps));
  return { ...hook, deps: fullDeps };
}

describe("useUpload", () => {
  it("runs the whole flow and ends when the dataset is registered", async () => {
    const getDataset = vi.fn().mockRejectedValueOnce(notFound()).mockResolvedValue({});
    const { result, deps } = setup({ createUpload: vi.fn().mockResolvedValue(form), getDataset });
    await act(() => result.current.start(file));
    expect(result.current.state).toEqual({ phase: "done", datasetId: "ds-1", registered: true });
    expect(getDataset).toHaveBeenCalledTimes(2);
    expect(deps.sleep).toHaveBeenCalledTimes(1);
    expect(deps.upload).toHaveBeenCalledWith(form, file, expect.objectContaining({ onProgress: expect.any(Function) }));
  });

  it("shows progress while uploading", async () => {
    const upload = deferred<void>();
    const { result } = setup(
      { createUpload: vi.fn().mockResolvedValue(form), getDataset: vi.fn().mockResolvedValue({}) },
      { upload: vi.fn((_f, _file, options) => { options.onProgress?.(0.4); return upload.promise; }) },
    );
    let flow!: Promise<void>;
    await act(async () => { flow = result.current.start(file); });
    expect(result.current.state).toEqual({ phase: "uploading", progress: 0.4 });
    await act(async () => { upload.resolve(); await flow; });
    expect(result.current.state.phase).toBe("done");
  });

  it("rejects a wrong file before calling the API", async () => {
    const createUpload = vi.fn();
    const { result } = setup({ createUpload });
    await act(() => result.current.start(new File(["x"], "notes.txt")));
    expect(result.current.state).toMatchObject({ phase: "error", message: expect.stringContaining(".csv") });
    expect(createUpload).not.toHaveBeenCalled();
  });

  it("shows the API's message when it refuses the upload", async () => {
    const { result, deps } = setup({ createUpload: vi.fn().mockRejectedValue(new ApiError(400, "bad_request", "filename must end in .csv or .json")) });
    await act(() => result.current.start(file));
    expect(result.current.state).toEqual({ phase: "error", message: "filename must end in .csv or .json" });
    expect(deps.upload).not.toHaveBeenCalled();
  });

  it("shows the S3 error and does not wait for the dataset", async () => {
    const getDataset = vi.fn();
    const { result } = setup(
      { createUpload: vi.fn().mockResolvedValue(form), getDataset },
      { upload: vi.fn().mockRejectedValue(new UploadError("S3 rejected the file because it is empty.")) },
    );
    await act(() => result.current.start(file));
    expect(result.current.state).toEqual({ phase: "error", message: "S3 rejected the file because it is empty." });
    expect(getDataset).not.toHaveBeenCalled();
  });

  it("still succeeds, with registered=false, if the dataset never appears", async () => {
    const getDataset = vi.fn().mockRejectedValue(notFound());
    const { result, deps } = setup({ createUpload: vi.fn().mockResolvedValue(form), getDataset });
    await act(() => result.current.start(file));
    expect(result.current.state).toEqual({ phase: "done", datasetId: "ds-1", registered: false });
    expect(getDataset).toHaveBeenCalledTimes(15);
    expect(deps.sleep).toHaveBeenCalledTimes(14); // no pointless wait after the last try
  });

  it("returns to idle when cancelled", async () => {
    const createUpload = vi.fn((_name: string, signal?: AbortSignal) =>
      new Promise<UploadForm>((_resolve, reject) => signal?.addEventListener("abort", () => reject(new DOMException("x", "AbortError")))),
    );
    const { result } = setup({ createUpload });
    let flow!: Promise<void>;
    await act(async () => { flow = result.current.start(file); });
    expect(result.current.state.phase).toBe("requesting");
    await act(async () => { result.current.cancel(); await flow; });
    expect(result.current.state).toEqual({ phase: "idle" });
  });

  it("can start over after an error", async () => {
    const createUpload = vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValue(form);
    const { result } = setup({ createUpload, getDataset: vi.fn().mockResolvedValue({}) });
    await act(() => result.current.start(file));
    expect(result.current.state.phase).toBe("error");
    await act(() => result.current.start(file));
    expect(result.current.state.phase).toBe("done");
  });
});
