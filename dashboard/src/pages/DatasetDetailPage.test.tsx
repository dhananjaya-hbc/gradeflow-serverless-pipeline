import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type ApiClient } from "../api/client";
import { ApiProvider } from "../api/context";
import type { AnomaliesResponse, DatasetDetail, StatsResponse } from "../api/types";
import anomaliesFixture from "../test/fixtures/anomalies.demo-030.json";
import completedFixture from "../test/fixtures/dataset.demo-030.json";
import failedFixture from "../test/fixtures/dataset.demo-014.json";
import statsFixture from "../test/fixtures/stats.demo-030.json";
import failedStatsFixture from "../test/fixtures/stats.demo-014.json";
import { DatasetDetailPage } from "./DatasetDetailPage";

const completed = completedFixture as unknown as DatasetDetail;
const failed = failedFixture as unknown as DatasetDetail;
const stats = statsFixture as unknown as StatsResponse;
const failedStats = failedStatsFixture as unknown as StatsResponse;
const anomalies = anomaliesFixture as unknown as AnomaliesResponse;

function makeClient(overrides: Partial<Record<keyof ApiClient, unknown>> = {}) {
  return {
    getDataset: vi.fn().mockResolvedValue(completed),
    getStats: vi.fn().mockResolvedValue(stats),
    getAnomalies: vi.fn().mockResolvedValue(anomalies),
    ...overrides,
  } as unknown as ApiClient & Record<"getDataset" | "getStats" | "getAnomalies", ReturnType<typeof vi.fn>>;
}

function renderPage(client: ApiClient, id = "demo-030") {
  return render(
    <ApiProvider client={client}>
      <MemoryRouter initialEntries={[`/datasets/${id}`]}>
        <Routes>
          <Route path="/" element={<p>list</p>} />
          <Route path="/datasets/:id" element={<DatasetDetailPage />} />
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

function anomalyTable(): HTMLElement {
  return screen.getByRole("heading", { name: "Anomalies" }).parentElement!.querySelector("table")!;
}

async function advance(ms: number) {
  for (let elapsed = 0; elapsed < ms; elapsed += 1_000) {
    await act(() => vi.advanceTimersByTimeAsync(Math.min(1_000, ms - elapsed)));
  }
}

describe("DatasetDetailPage: completed dataset (real API responses)", () => {
  it("shows the summary, tables and validation report with the real numbers", async () => {
    renderPage(makeClient());
    expect(await screen.findByRole("heading", { name: "sample_anoms.csv" })).toBeInTheDocument();
    expect(await screen.findByText("Top students")).toBeInTheDocument();

    const cards = screen.getByText("Average score").closest("dl")!;
    expect(within(cards).getByText("62.43")).toBeInTheDocument();
    expect(within(cards).getByText("78.34%")).toBeInTheDocument();
    expect(within(cards).getByText("60")).toBeInTheDocument(); // students
    expect(within(cards).getByText("904")).toBeInTheDocument();
    expect(within(cards).getByText("891")).toBeInTheDocument();
    expect(within(cards).getByText("3 duplicates removed")).toBeInTheDocument();

    const top = screen.getByText("Top students").parentElement!;
    expect(within(top).getByText("Malsha Silva")).toBeInTheDocument();
    expect(within(top).getByText("93.19")).toBeInTheDocument();
    expect(screen.getByText("62.75")).toBeInTheDocument(); // class 10-B
    expect(screen.getByText("10-B")).toBeInTheDocument();
    expect(screen.getByText("row 628: score '150' must be a number 0-100")).toBeInTheDocument();
  });

  it("gives every chart a table with the same numbers", async () => {
    renderPage(makeClient());
    await screen.findByText("Top students");
    const tables = screen.getAllByText(/Show .* as a table/);
    expect(tables).toHaveLength(2);
    // English, T1 is 63.15 in the real statistics
    expect(screen.getAllByText("63.15").length).toBeGreaterThan(0);
  });

  it("lists the anomalies in rank order with readable method and pattern labels", async () => {
    renderPage(makeClient());
    const table = (await screen.findByText("S00045")).closest("table")!;
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(9);
    expect(within(rows[0]!).getByText("Duplicate ID")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Term jump")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Spike")).toBeInTheDocument();
    expect(within(rows[5]!).getByText("Sustained")).toBeInTheDocument();
    expect(within(rows[8]!).getByText("Unusual student")).toBeInTheDocument();
    expect(screen.getByText(/Showing 9 of 9 stored/)).toBeInTheDocument();
  });

  it("asks the server for one method when the filter changes", async () => {
    const client = makeClient();
    client.getAnomalies.mockResolvedValueOnce(anomalies).mockResolvedValueOnce({
      ...anomalies,
      items: anomalies.items.filter((a) => a.method === "isolation_forest"),
    });
    renderPage(client);
    await screen.findByText("S00045");
    await userEvent.selectOptions(screen.getByLabelText("Method"), "isolation_forest");
    await screen.findAllByText("Unusual student");
    expect(client.getAnomalies).toHaveBeenLastCalledWith(
      "demo-030",
      { method: "isolation_forest", limit: 25 },
      expect.any(AbortSignal),
    );
    expect(screen.queryByText("S00045")).not.toBeInTheDocument();
  });

  it("searches the loaded anomalies by student id or name, and says when nothing matches", async () => {
    renderPage(makeClient());
    await screen.findByText("S00045");
    await userEvent.type(screen.getByLabelText("Student"), "s00055");
    const rows = within(anomalyTable()).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3); // two term jumps and one forest finding
    expect(screen.getByText(/Showing 3 of 9 loaded/)).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Student"));
    await userEvent.type(screen.getByLabelText("Student"), "nobody");
    expect(screen.getByText(/No loaded anomaly matches “nobody”/)).toBeInTheDocument();
  });

  it("loads more anomalies with the cursor", async () => {
    const client = makeClient();
    client.getAnomalies
      .mockResolvedValueOnce({ ...anomalies, items: anomalies.items.slice(0, 3), next_cursor: "c1" })
      .mockResolvedValueOnce({ ...anomalies, items: anomalies.items.slice(2), next_cursor: null });
    renderPage(client);
    await screen.findByText("S00045");
    expect(within(anomalyTable()).getAllByRole("row").slice(1)).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByText("Sustained");
    expect(within(anomalyTable()).getAllByRole("row").slice(1)).toHaveLength(9); // rank 3 not repeated
    expect(client.getAnomalies).toHaveBeenLastCalledWith("demo-030", { method: null, limit: 25, cursor: "c1" });
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("does not call the anomalies endpoint when nothing was stored", async () => {
    const none = { ...completed, anomaly_count: 0, anomalies: { count: 0, stored: 0, ml_skipped: null } };
    const client = makeClient({ getDataset: vi.fn().mockResolvedValue(none) });
    renderPage(client);
    await screen.findByText("Top students");
    expect(client.getAnomalies).not.toHaveBeenCalled();
  });

  it("explains a skipped machine-learning check", async () => {
    const client = makeClient({
      getAnomalies: vi.fn().mockResolvedValue({ ...anomalies, ml_skipped: "only 12 students, at least 20 are needed" }),
    });
    renderPage(client);
    expect(await screen.findByText(/machine-learning check was skipped: only 12 students/)).toBeInTheDocument();
  });

  it("keeps the dataset on screen if the statistics fail, and can retry", async () => {
    const client = makeClient();
    client.getStats.mockRejectedValueOnce(new Error("stats down")).mockResolvedValue(stats);
    renderPage(client);
    expect(await screen.findByRole("alert")).toHaveTextContent("stats down");
    expect(screen.getByRole("heading", { name: "sample_anoms.csv" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Top students")).toBeInTheDocument();
  });
});

describe("DatasetDetailPage: other states", () => {
  afterEach(() => vi.useRealTimers());

  it("shows the reason for a failed dataset and never asks for statistics", async () => {
    const client = makeClient({ getDataset: vi.fn().mockResolvedValue(failed), getStats: vi.fn().mockResolvedValue(failedStats) });
    renderPage(client, "demo-014");
    expect(await screen.findByRole("alert")).toHaveTextContent("missing required columns: student_name");
    expect(screen.getByText(/kept in quarantine/)).toBeInTheDocument();
    expect(client.getStats).not.toHaveBeenCalled();
    expect(client.getAnomalies).not.toHaveBeenCalled();
  });

  it("links a duplicate to the dataset it repeats", async () => {
    const duplicate = { ...completed, dataset_id: "demo-004", status: "DUPLICATE", duplicate_of: "demo-003" } as DatasetDetail;
    const client = makeClient({ getDataset: vi.fn().mockResolvedValue(duplicate) });
    renderPage(client, "demo-004");
    const link = await screen.findByRole("link", { name: "demo-003" });
    expect(link).toHaveAttribute("href", "/datasets/demo-003");
    expect(client.getStats).not.toHaveBeenCalled();
  });

  it("says so when the dataset does not exist", async () => {
    const client = makeClient({ getDataset: vi.fn().mockRejectedValue(new ApiError(404, "not_found", "no such dataset")) });
    renderPage(client, "nope");
    expect(await screen.findByRole("heading", { name: "Dataset not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to datasets" })).toHaveAttribute("href", "/");
  });

  it("shows other errors with a retry", async () => {
    const getDataset = vi.fn().mockRejectedValueOnce(new ApiError(500, "internal", "Something broke")).mockResolvedValue(completed);
    renderPage(makeClient({ getDataset }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something broke");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "sample_anoms.csv" })).toBeInTheDocument();
  });

  it("refreshes while processing, then loads the statistics and stops", async () => {
    vi.useFakeTimers();
    const processing = { ...completed, status: "PROCESSING" } as DatasetDetail;
    const getDataset = vi
      .fn()
      .mockResolvedValueOnce(processing)
      .mockResolvedValueOnce(processing)
      .mockResolvedValue(completed);
    const client = makeClient({ getDataset });
    renderPage(client);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText(/still being processed/)).toBeInTheDocument();
    expect(client.getStats).not.toHaveBeenCalled();

    await advance(10_000);
    expect(getDataset).toHaveBeenCalledTimes(3);
    expect(client.getStats).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/still being processed/)).not.toBeInTheDocument();
    expect(screen.getByText("Top students")).toBeInTheDocument();

    await advance(120_000);
    expect(getDataset).toHaveBeenCalledTimes(3); // finished: no more checks
  });

  it("gives up after 10 minutes of processing and says so", async () => {
    vi.useFakeTimers();
    const processing = { ...completed, status: "PROCESSING" } as DatasetDetail;
    const getDataset = vi.fn().mockResolvedValue(processing);
    renderPage(makeClient({ getDataset }));
    await act(() => vi.advanceTimersByTimeAsync(0));
    await advance(10 * 60_000 + 1_000);
    expect(screen.getByText(/Automatic refresh stopped/)).toBeInTheDocument();
    const calls = getDataset.mock.calls.length;
    await advance(5 * 60_000);
    expect(getDataset.mock.calls.length).toBe(calls);
  });

  it("does not show the previous dataset's data after navigating to another one", async () => {
    const client = makeClient();
    render(
      <ApiProvider client={client}>
        <MemoryRouter initialEntries={["/datasets/demo-030"]}>
          <Link to="/datasets/other">go</Link>
          <Routes>
            <Route path="/datasets/:id" element={<DatasetDetailPage />} />
          </Routes>
        </MemoryRouter>
      </ApiProvider>,
    );
    await screen.findByText("Top students");
    client.getDataset.mockReturnValue(new Promise<DatasetDetail>(() => undefined));
    await userEvent.click(screen.getByRole("link", { name: "go" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Loading dataset");
    expect(screen.queryByText("Top students")).not.toBeInTheDocument();
    expect(client.getDataset).toHaveBeenLastCalledWith("other", expect.any(AbortSignal));
  });
});
