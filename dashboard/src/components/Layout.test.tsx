import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { useTitle } from "../hooks/useTitle";
import { Layout } from "./Layout";
import { TableScroll } from "./TableScroll";

function Page({ title }: { title: string }) {
  useTitle(title);
  return <h1>{title}</h1>;
}

function renderLayout(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Page title="Datasets" />} />
          <Route path="/upload" element={<Page title="Upload" />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Layout", () => {
  it("starts with a skip link that targets the main content", () => {
    renderLayout();
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(document.body.firstElementChild?.firstElementChild).toBe(skip);
  });

  it("marks the current page in the navigation", async () => {
    renderLayout();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav.querySelector('[aria-current="page"]')).toHaveTextContent("Datasets");
    await userEvent.click(screen.getByRole("link", { name: "Upload" }));
    expect(nav.querySelector('[aria-current="page"]')).toHaveTextContent("Upload");
    expect(screen.getByRole("heading", { name: "Upload" })).toBeInTheDocument();
  });

  it("puts the page name in the tab title and resets it on leaving", async () => {
    const { unmount } = renderLayout();
    expect(document.title).toBe("Datasets · GradeFlow");
    await userEvent.click(screen.getByRole("link", { name: "Upload" }));
    expect(document.title).toBe("Upload · GradeFlow");
    unmount();
    expect(document.title).toBe("GradeFlow");
  });
});

describe("TableScroll", () => {
  it("is a labelled, keyboard-focusable region", () => {
    render(
      <TableScroll label="Anomalies">
        <table />
      </TableScroll>,
    );
    const region = screen.getByRole("region", { name: "Anomalies" });
    expect(region).toHaveAttribute("tabindex", "0");
  });
});
