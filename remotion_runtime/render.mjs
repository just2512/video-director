#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {setTimeout as delay} from "node:timers/promises";
import {bundle} from "@remotion/bundler";
import {renderMedia, selectComposition} from "@remotion/renderer";

const runtimeDir = path.dirname(fileURLToPath(import.meta.url));
const sourceDir = path.join(runtimeDir, "src");
const cacheDir = path.join(runtimeDir, ".cache");
const remotionVersion = "4.0.536";

const parseArgs = (args) => {
  const out = {};
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--smoke") out.smoke = true;
    else if (arg === "--props" || arg === "--out") {
      if (!args[i + 1]) throw new Error(arg + " needs a path");
      out[arg.slice(2)] = path.resolve(args[++i]);
    } else throw new Error("Unknown argument: " + arg);
  }
  return out;
};

const sourceHash = async (dir) => {
  const entries = [];
  const walk = async (current) => {
    for (const name of (await fs.readdir(current)).sort()) {
      const absolute = path.join(current, name);
      const stat = await fs.stat(absolute);
      if (stat.isDirectory()) await walk(absolute);
      else entries.push([path.relative(runtimeDir, absolute).replaceAll("\\", "/"), await fs.readFile(absolute)]);
    }
  };
  await walk(dir);
  const digest = crypto.createHash("sha256");
  digest.update(remotionVersion);
  for (const item of entries) { digest.update(item[0]); digest.update(item[1]); }
  return digest.digest("hex").slice(0, 20);
};

const ensureBundle = async () => {
  await fs.mkdir(cacheDir, {recursive: true});
  const fingerprint = await sourceHash(sourceDir);
  const outDir = path.join(cacheDir, "bundle-" + fingerprint);
  const marker = path.join(outDir, ".director-bundle");
  const isReady = async () => {
    try { return (await fs.readFile(marker, "utf8")) === fingerprint; } catch { return false; }
  };
  if (await isReady()) return outDir;
  const lock = path.join(cacheDir, "bundle-" + fingerprint + ".lock");
  let locked = false;
  for (let attempt = 0; attempt < 240; attempt++) {
    try { await fs.mkdir(lock); locked = true; break; }
    catch (error) {
      if (error.code !== "EEXIST") throw error;
      try {
        const stat = await fs.stat(lock);
        if (Date.now() - stat.mtimeMs > 300000) { await fs.rm(lock, {recursive: true, force: true}); continue; }
      } catch {}
      if (await isReady()) return outDir;
      await delay(250);
    }
  }
  if (!locked) throw new Error("Timed out waiting for Remotion bundle lock");
  try {
    await fs.rm(outDir, {recursive: true, force: true});
    await bundle({entryPoint: path.join(sourceDir, "index.tsx"), outDir, logLevel: "error"});
    await fs.writeFile(marker, fingerprint, "utf8");
  } finally { await fs.rm(lock, {recursive: true, force: true}); }
  return outDir;
};

const smokeProps = {
  width: 640, height: 360, fps: 30, durationInFrames: 45, audioUrl: null, audioStartFrame: 0,
  shots: [{
    id: "smoke-shot", recipeId: "shot.data-number-burst", category: "data",
    intent: "renderer smoke test", text: "Remotion renderer is connected",
    startFrame: 0, durationFrames: 45, startMs: 0, transition: null, characters: [],
    layers: [
      {id: "smoke-bg", kind: "background", background: "#f4f1e9", zIndex: 0},
      {id: "smoke-title", kind: "text", text: "Remotion renderer is connected", zIndex: 2,
       motion: [{property: "opacity", keyframes: [{at: 0, value: 0}, {at: 1, value: 1, easing: {type: "cubic", mode: "out"}}]}]},
    ],
  }],
};

const run = async () => {
  const args = parseArgs(process.argv.slice(2));
  let props;
  let outputLocation;
  const cleanupOutput = !!args.smoke;
  if (args.smoke) {
    props = smokeProps;
    outputLocation = path.join(runtimeDir, ".remotion-smoke.mp4");
  } else {
    if (!args.props || !args.out) throw new Error("Usage: node render.mjs --props <json> --out <mp4>");
    props = JSON.parse(await fs.readFile(args.props, "utf8"));
    outputLocation = args.out;
  }
  if (!props || !Array.isArray(props.shots) || !Number.isFinite(Number(props.durationInFrames)) || props.durationInFrames < 1) {
    throw new Error("Invalid render props: durationInFrames and shots are required");
  }
  await fs.mkdir(path.dirname(outputLocation), {recursive: true});
  const serveUrl = await ensureBundle();
  const inputProps = {
    width: Number(props.width) || 1080, height: Number(props.height) || 1920,
    fps: Number(props.fps) || 30, durationInFrames: Math.floor(Number(props.durationInFrames)),
    shots: props.shots, audioUrl: props.audioUrl || null, audioStartFrame: Number(props.audioStartFrame) || 0,
  };
  try {
    const composition = await selectComposition({serveUrl, id: "CreativeVideo", inputProps, logLevel: "error"});
    await renderMedia({
      composition, serveUrl, codec: "h264", crf: 18, pixelFormat: "yuv420p",
      outputLocation, inputProps, logLevel: "error", concurrency: 2,
    });
    const stat = await fs.stat(outputLocation);
    if (stat.size < 1000) throw new Error("Render output is implausibly small");
    process.stdout.write(JSON.stringify({
      ok: true, output: outputLocation, bytes: stat.size, width: composition.width,
      height: composition.height, fps: composition.fps,
      durationInFrames: composition.durationInFrames, remotionVersion,
    }) + "\n");
  } finally {
    if (cleanupOutput) await fs.rm(outputLocation, {force: true});
  }
};

run().catch((error) => {
  process.stderr.write((error && error.stack) ? error.stack + "\n" : String(error) + "\n");
  process.exitCode = 1;
});
