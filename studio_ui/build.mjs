import {build} from "esbuild";
import {dirname, resolve} from "node:path";
import {fileURLToPath} from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(here, "..");

await build({
  entryPoints: [resolve(repositoryRoot, "remotion_runtime/src/StudioCanvasInspector.tsx")],
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2022",
  jsx: "automatic",
  nodePaths: [resolve(here, "node_modules")],
  outfile: resolve(repositoryRoot, "webui/static/js/film-studio-react.js"),
});
