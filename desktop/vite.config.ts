import { defineConfig } from "vite";

export default defineConfig({
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    // Tauri/Cargo owns the Rust build tree. Vite must not recursively
    // watch src-tauri/target because Cargo creates and replaces locked
    // .exe files there during compilation on Windows. Watching that tree
    // can make Node's fs.watch fail with EBUSY and terminate Tauri startup
    // even though the Rust build itself is healthy.
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
  build: {
    target: "es2022",
  },
});
