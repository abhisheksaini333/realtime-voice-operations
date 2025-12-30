import * as esbuild from "esbuild";
import { readFile, writeFile, rm } from "node:fs/promises";
import { watch } from "node:fs";
import { dirname, basename, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const watching = process.argv.includes("--watch");
await rm(join(root, "dist"), { recursive: true, force: true });
const options = {
  absWorkingDir: root,
  entryPoints: ["src.jsx"],
  outdir: "dist/assets",
  entryNames: "app-[hash]",
  bundle: true,
  format: "esm",
  minify: true,
  metafile: true,
  target: ["chrome107", "firefox104", "safari16"],
  define: { "process.env.NODE_ENV": '"production"' },
  logLevel: "info",
  plugins: [
    {
      name: "static-html",
      setup(build) {
        build.onEnd(async (result) => {
          if (result.errors.length) return;
          const outputs = Object.keys(result.metafile.outputs);
          const script = outputs.find((file) => file.endsWith(".js"));
          const style = outputs.find((file) => file.endsWith(".css"));
          if (!script || !style)
            throw new Error("Expected a script and stylesheet");
          const template = await readFile(join(root, "index.html"), "utf8");
          const html = template
            .replace('src="/src.jsx"', `src="/assets/${basename(script)}"`)
            .replace(
              "</head>",
              `<link rel="stylesheet" href="/assets/${basename(style)}"/></head>`,
            );
          await writeFile(join(root, "dist/index.html"), html);
        });
      },
    },
  ],
};
if (watching) {
  const context = await esbuild.context(options);
  await context.watch();
  const templateWatcher = watch(join(root, "index.html"), () => {
    context.rebuild().catch((error) => console.error(error.message));
  });
  async function stop() {
    templateWatcher.close();
    await context.dispose();
    process.exit(0);
  }
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
} else {
  await esbuild.build(options);
}
