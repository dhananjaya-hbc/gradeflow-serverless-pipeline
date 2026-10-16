import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../api/client";
import { ApiProvider } from "../api/context";
import { UploadPage } from "./UploadPage";

const upload = vi.hoisted(() => vi.fn());
vi.mock("../lib/upload", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/upload")>()),
  uploadToS3: upload,
}));

const form = {
  dataset_id: "ds-1",
  key: "uploads/ds-1/a.csv",
  upload: { method: "POST", url: "https://s3.example/", fields: {} },
  max_bytes: 1,
  expires_in: 1,
};

function renderPage(client: Partial<ApiClient>) {
  return render(
    <ApiProvider client={client as ApiClient}>
      <MemoryRouter>
        <UploadPage />
      </MemoryRouter>
    </ApiProvider>,
  );
}

describe("UploadPage", () => {
  beforeEach(() => upload.mockReset());

  it("uploads a chosen CSV and offers the next steps", async () => {
    upload.mockResolvedValue(undefined);
    renderPage({ createUpload: vi.fn().mockResolvedValue(form), getDataset: vi.fn().mockResolvedValue({}) });
    await userEvent.upload(screen.getByLabelText(/Drop a file here/), new File(["a,b\n1,2\n"], "a.csv", { type: "text/csv" }));
    expect(await screen.findByText("Upload complete.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to datasets" })).toHaveAttribute("href", "/");
    await userEvent.click(screen.getByRole("button", { name: "Upload another" }));
    expect(screen.getByLabelText(/Drop a file here/)).toBeEnabled();
  });

  it("shows an alert for a file of the wrong type (dropped, so the accept filter does not apply)", async () => {
    const createUpload = vi.fn();
    renderPage({ createUpload });
    const zone = screen.getByLabelText(/Drop a file here/).parentElement!;
    const file = new File(["x"], "notes.txt");
    const dropEvent = new Event("drop", { bubbles: true, cancelable: true });
    Object.assign(dropEvent, { dataTransfer: { files: [file] } });
    zone.dispatchEvent(dropEvent);
    expect(await screen.findByRole("alert")).toHaveTextContent("Only .csv and .json");
    expect(createUpload).not.toHaveBeenCalled();
  });

  it("shows the failure with a way to start again", async () => {
    renderPage({ createUpload: vi.fn().mockRejectedValue(new Error("The service is busy, try again.")) });
    await userEvent.upload(screen.getByLabelText(/Drop a file here/), new File(["a"], "a.csv"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The service is busy");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(upload).not.toHaveBeenCalled();
  });
});
