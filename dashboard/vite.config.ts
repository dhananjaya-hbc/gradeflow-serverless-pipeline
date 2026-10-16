/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // Relative asset paths, so the build works from any CloudFront path or bucket prefix.
  base: "./",
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: { include: [/styles\.css/] }, // processed so styles.test.ts can read the real stylesheet
  },
});
