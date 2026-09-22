import {defineConfig} from "vite";

const productionFrontend =
  "../custom_components/extended_openai_conversation_responses/frontend";

export default defineConfig({
  // HA serves this directory below its integration URL, not at the site root.
  base: "./",
  build: {
    rollupOptions: {
      input: {
        management: `${productionFrontend}/management-panel.js`,
      },
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        manualChunks(id) {
          const coreHelpers = [
            "management-enhancement-state.js",
            "management-data-state.js",
            "management-confirmation-scope.js",
            "management-dialogs.js",
            "agent-config-help.js",
            "usage-format.js",
          ];
          if (coreHelpers.some((name) => id.endsWith(`/frontend/${name}`))) {
            return "management-core";
          }
        },
      },
    },
    outDir: `${productionFrontend}/dist`,
    emptyOutDir: true,
    manifest: "manifest.json",
    target: "es2022",
    minify: "oxc",
    sourcemap: false,
  },
});
