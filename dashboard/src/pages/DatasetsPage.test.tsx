import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiProvider } from "../api/context";
import type { ApiClient } from "../api/client";
import type { DatasetSummary, DatasetsPage as Page } from "../api/types";
import page1 from "../test/fixtures/datasets.page1.json";
import { DatasetsPage } from "./DatasetsPage";

const fixture = page1 as unknown as Page;

/** Advance fake time in 1 s steps, one `act` each: React applies state updates (and so schedules
 * the next poll) only when an `act` ends, so one long advance would run just one poll. */
async function advance(ms: number) {
  for (let elapsed = 0; elapsed < ms; elapsed += 1_000) {
    await act(() => vi.advanceTimersByTimeAsync(Math.min(1_000, ms - elapsed)));
  }
}

function summary(id: string, status: string): DatasetSummary {
  return { ...fixture.datasets[0]!, dataset_id: id, filename: `${id}.csv`, status } as DatasetSummary;
}

function renderPage(listDatasets: ReturnType<typeof vi.fn>) {
  const client = { listDatasets } as unknown as ApiClient;
  return render(
    <ApiProvider client={client}>
      <MemoryRouter>
        <DatasetsPage />
      </MemoryRouter>
    </ApiProvider>,
  );
}

describe("DatasetsPage", () => {
  afterEach(() => vi.useRealTimers());

  it("shows the real datasets from the API with status badges", async () => {
    const list = vi.fn().mockResolvedValue({ ...fixture, next_cursor: null });
    renderPage(list);
    expect(screen.getByRole("status")).toHaveTextContent("Loading datasets");
    expect(await screen.findByText(fixture.datasets[0]!.filename as string)).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(fixture.datasets.length + 1);
    expect(screen.queryByText("Load more")).not.toBeInTheDocument();
  });

  it("shows an empty state with a link to upload", async () => {
    renderPage(vi.fn().mockResolvedValue({ datasets: [], next_cursor: null }));
    expect(await screen.findByText(/No datasets yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload a file" })).toHaveAttribute("href", "/upload");
  });

  it("shows an error and retries", async () => {
    const list = vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValue({ datasets: [], next_cursor: null });
    renderPage(list);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/No datasets yet/)).toBeInTheDocument();
  });

  it("loads more with the cursor and does not repeat rows", async () => {
    const list = vi
      .fn()
      .mockResolvedValueOnce({ datasets: [summary("a", "COMPLETED")], next_cursor: "c1" })
      .mockResolvedValueOnce({ datasets: [summary("a", "COMPLETED"), summary("b", "FAILED")], next_cursor: null });
    renderPage(list);
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }));
    expect(await screen.findByText("b.csv")).toBeInTheDocument();
    expect(screen.getAllByText("a.csv")).toHaveLength(1);
    expect(list).toHaveBeenLastCalledWith({ limit: 20, cursor: "c1" });
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  describe("auto-refresh", () => {
    beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: false }));

    it("polls while a dataset is processing and stops once everything is finished", async () => {
      const list = vi
        .fn()
        .mockResolvedValueOnce({ datasets: [summary("a", "PROCESSING")], next_cursor: null })
        .mockResolvedValueOnce({ datasets: [summary("a", "PROCESSING")], next_cursor: null })
        .mockResolvedValue({ datasets: [summary("a", "COMPLETED")], next_cursor: null });
      renderPage(list);
      await act(() => vi.advanceTimersByTimeAsync(0));
      expect(list).toHaveBeenCalledTimes(1);

      await advance(5_000);
      expect(list).toHaveBeenCalledTimes(2);
      await advance(5_000);
      expect(list).toHaveBeenCalledTimes(3);
      expect(screen.getByText("Completed")).toBeInTheDocument();

      await advance(120_000);
      expect(list).toHaveBeenCalledTimes(3); // finished: no more calls
    });

    it("never polls when nothing is processing", async () => {
      const list = vi.fn().mockResolvedValue({ datasets: [summary("a", "COMPLETED")], next_cursor: null });
      renderPage(list);
      await act(() => vi.advanceTimersByTimeAsync(0));
      await advance(60_000);
      expect(list).toHaveBeenCalledTimes(1);
    });

    it("gives up after 10 minutes and says so", async () => {
      const list = vi.fn().mockResolvedValue({ datasets: [summary("a", "PROCESSING")], next_cursor: null });
      renderPage(list);
      await act(() => vi.advanceTimersByTimeAsync(0));
      await advance(10 * 60_000 + 1_000);
      const calls = list.mock.calls.length;
      expect(calls).toBeGreaterThan(10);
      expect(screen.getByText(/Automatic refresh stopped/)).toBeInTheDocument();
      await advance(5 * 60_000);
      expect(list.mock.calls.length).toBe(calls);
    });

    it("keeps the list and shows a message when a background refresh fails", async () => {
      const list = vi
        .fn()
        .mockResolvedValueOnce({ datasets: [summary("a", "PROCESSING")], next_cursor: null })
        .mockRejectedValueOnce(new Error("temporary"))
        .mockResolvedValue({ datasets: [summary("a", "PROCESSING")], next_cursor: null });
      renderPage(list);
      await act(() => vi.advanceTimersByTimeAsync(0));
      await advance(5_000);
      expect(screen.getByText("a.csv")).toBeInTheDocument();
      expect(screen.getByRole("alert")).toHaveTextContent("temporary");
      await advance(5_000);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("does not poll while the tab is hidden", async () => {
      const list = vi.fn().mockResolvedValue({ datasets: [summary("a", "PROCESSING")], next_cursor: null });
      const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
      renderPage(list);
      await act(() => vi.advanceTimersByTimeAsync(0));
      await advance(30_000);
      expect(list).toHaveBeenCalledTimes(1);
      hidden.mockReturnValue(false);
      await advance(5_000);
      expect(list).toHaveBeenCalledTimes(2);
      hidden.mockRestore();
    });
  });
});
