// Upload the monthly-changing promap data to Vercel Blob.
// Run by the GitHub Action after create_sophisticated_map.py regenerates data.
// Geometries are static and are NOT uploaded here (uploaded once, never change).
import { put } from "@vercel/blob";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Resolve relative to this script so it works in CI and locally (handles spaces in path).
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = path.join(__dirname, "..", "output");

const FILES = [
  { name: "promap_data.js", contentType: "application/javascript" },
  { name: "long_term_changes.json", contentType: "application/json" },
];

const token = process.env.BLOB_READ_WRITE_TOKEN;
if (!token) {
  console.error("BLOB_READ_WRITE_TOKEN env var not set");
  process.exit(1);
}

for (const { name, contentType } of FILES) {
  const filePath = path.join(OUTPUT_DIR, name);
  const data = await readFile(filePath);
  console.log(`Uploading ${name} (${(data.length / 1024 / 1024).toFixed(2)} MB)...`);
  const result = await put(`promap/${name}`, data, {
    access: "public",
    token,
    contentType,
    addRandomSuffix: false,
    allowOverwrite: true,
    multipart: true,
    // 1-day browser cache: these files change ~monthly, so a short max-age means
    // returning visitors pick up new data within a day (etag still gives cheap 304s).
    cacheControlMaxAge: 86400,
  });
  console.log(`  ✓ ${result.url}`);
}
console.log("Done.");
