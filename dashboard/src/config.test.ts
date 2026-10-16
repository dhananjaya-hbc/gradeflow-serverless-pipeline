import { describe, expect, it, vi } from "vitest";
import { ConfigError, loadConfig } from "./config";
import { jsonResponse } from "./test/helpers";

const fetchReturning = (response: Response) => vi.fn(async () => response) as unknown as typeof fetch;

describe("loadConfig", () => {
  it("reads the API address and key", async () => {
    const config = await loadConfig(
      fetchReturning(jsonResponse(200, { apiUrl: "https://abc.execute-api.ap-south-1.amazonaws.com/v1", apiKey: "k-123" })),
      "config.json",
    );
    expect(config).toEqual({ apiUrl: "https://abc.execute-api.ap-south-1.amazonaws.com/v1", apiKey: "k-123" });
  });

  it("removes trailing slashes from the address and spaces from the key", async () => {
    const config = await loadConfig(
      fetchReturning(jsonResponse(200, { apiUrl: "https://api.example.com/v1///", apiKey: "  secret  " })),
      "config.json",
    );
    expect(config).toEqual({ apiUrl: "https://api.example.com/v1", apiKey: "secret" });
  });

  it("asks for a fresh copy, never a cached one", async () => {
    const fetchFn = vi.fn(async () => jsonResponse(200, { apiUrl: "https://a.example.com", apiKey: "k" }));
    await loadConfig(fetchFn as unknown as typeof fetch, "config.json");
    expect(fetchFn).toHaveBeenCalledWith("config.json", { cache: "no-store" });
  });

  it("explains how to fix a missing file", async () => {
    const error = await loadConfig(fetchReturning(new Response("nope", { status: 404 })), "config.json").catch((e) => e);
    expect(error).toBeInstanceOf(ConfigError);
    expect(error.message).toMatch(/HTTP 404/);
    expect(error.message).toMatch(/deploy_dashboard\.sh/);
  });

  it("explains a dev server answering with index.html instead of JSON", async () => {
    const html = new Response("<!doctype html><html></html>", { status: 200, headers: { "Content-Type": "text/html" } });
    await expect(loadConfig(fetchReturning(html), "config.json")).rejects.toThrow(/not valid JSON/);
  });

  it("reports a network failure", async () => {
    const failing = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    await expect(loadConfig(failing, "config.json")).rejects.toThrow(/network error/);
  });

  it.each([
    ["no address", { apiKey: "k" }, /apiUrl/],
    ["an address that is not a URL", { apiUrl: "api.example.com", apiKey: "k" }, /apiUrl/],
    ["an address that is not a string", { apiUrl: 5, apiKey: "k" }, /apiUrl/],
    ["no key", { apiUrl: "https://a.example.com" }, /apiKey/],
    ["an empty key", { apiUrl: "https://a.example.com", apiKey: "   " }, /apiKey/],
    ["a key that is not a string", { apiUrl: "https://a.example.com", apiKey: 123 }, /apiKey/],
  ])("rejects %s", async (_name, body, message) => {
    await expect(loadConfig(fetchReturning(jsonResponse(200, body)), "config.json")).rejects.toThrow(message);
  });

  it("rejects JSON that is not an object", async () => {
    await expect(loadConfig(fetchReturning(jsonResponse(200, null)), "config.json")).rejects.toThrow(ConfigError);
    await expect(loadConfig(fetchReturning(jsonResponse(200, [1, 2])), "config.json")).rejects.toThrow(ConfigError);
  });
});
