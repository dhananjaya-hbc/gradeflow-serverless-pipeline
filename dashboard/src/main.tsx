import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { App } from "./App";
import { ApiProvider } from "./api/context";
import { ApiClient } from "./api/client";
import { ConfigError, loadConfig } from "./config";
import { ErrorBox } from "./components/ErrorBox";

const root = createRoot(document.getElementById("root")!);

loadConfig(fetch, "config.json")
  .then((config) => {
    root.render(
      <StrictMode>
        <ApiProvider client={new ApiClient(config)}>
          <HashRouter>
            <App />
          </HashRouter>
        </ApiProvider>
      </StrictMode>,
    );
  })
  .catch((error: unknown) => {
    const message = error instanceof ConfigError || error instanceof Error ? error.message : String(error);
    root.render(<ErrorBox title="The dashboard is not configured" message={message} />);
  });
