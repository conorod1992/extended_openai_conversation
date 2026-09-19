import {defineConfig} from "vite";

const productionFrontend =
  "../custom_components/extended_openai_conversation_responses/frontend";

export default defineConfig({
  build: {
    rollupOptions: {
      input: {
        management: `${productionFrontend}/management-panel.js`,
      },
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
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
