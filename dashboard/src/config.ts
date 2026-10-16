/** Runtime settings, loaded from config.json next to index.html (written by the deploy script). */

export interface AppConfig {
  apiUrl: string; // no trailing slash, e.g. https://abc123.execute-api.ap-south-1.amazonaws.com/v1
  apiKey: string;
}

export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

const DEFAULT_URL = `${import.meta.env.BASE_URL}config.json`;

export async function loadConfig(
  fetchFn: typeof fetch = (...args) => fetch(...args),
  url: string = DEFAULT_URL,
): Promise<AppConfig> {
  let response: Response;
  try {
    response = await fetchFn(url, { cache: "no-store" });
  } catch {
    throw new ConfigError("Could not load config.json (network error). Is the site reachable?");
  }
  if (!response.ok) {
    throw new ConfigError(
      `config.json was not found (HTTP ${response.status}). ` +
        "Run scripts/deploy_dashboard.sh, or for local development copy " +
        "public/config.example.json to public/config.json and fill it in.",
    );
  }

  let data: unknown;
  try {
    data = await response.json();
  } catch {
    // The dev server answers a missing file with index.html, which is not JSON.
    throw new ConfigError(
      "config.json is not valid JSON. For local development copy " +
        "public/config.example.json to public/config.json and fill it in.",
    );
  }

  const { apiUrl, apiKey } = (data ?? {}) as Record<string, unknown>;
  if (typeof apiUrl !== "string" || !/^https?:\/\/\S+$/.test(apiUrl)) {
    throw new ConfigError('config.json needs "apiUrl" (the API address, starting with https://).');
  }
  if (typeof apiKey !== "string" || apiKey.trim() === "") {
    throw new ConfigError('config.json needs "apiKey" (the API key value).');
  }
  return { apiUrl: apiUrl.replace(/\/+$/, ""), apiKey: apiKey.trim() };
}
