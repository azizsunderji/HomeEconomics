// Publishes the Metro Explorer data files to Vercel Blob, where the page at
// homeeconomics.us/metro-explorer reads them.
//
//   node metro-explorer/scripts/upload_to_blob.mjs --all
//   node metro-explorer/scripts/upload_to_blob.mjs aberdeen_sd.json index.json ...
//   node metro-explorer/scripts/upload_to_blob.mjs --from-file changed.txt
//
// Needs BLOB_READ_WRITE_TOKEN (the portal project's Blob store).
import { put } from "@vercel/blob";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DATA_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "data");
const PREFIX = "metro-explorer/data";
const CONCURRENCY = 12;

const token = process.env.BLOB_READ_WRITE_TOKEN;
if (!token) {
  console.error("BLOB_READ_WRITE_TOKEN env var not set");
  process.exit(1);
}

const args = process.argv.slice(2);
let names;
if (args[0] === "--all") {
  names = (await readdir(DATA_DIR)).filter((n) => n.endsWith(".json"));
} else if (args[0] === "--from-file") {
  names = (await readFile(args[1], "utf8")).split("\n");
} else {
  names = args;
}
names = [...new Set(names.map((n) => path.basename(n.trim())).filter((n) => n.endsWith(".json")))];
if (names.length === 0) {
  console.log("Nothing to upload.");
  process.exit(0);
}

console.log(`Uploading ${names.length} file(s) to ${PREFIX}/ ...`);
let done = 0;
const failed = [];
const queue = [...names];

async function worker() {
  for (let name = queue.shift(); name; name = queue.shift()) {
    try {
      const body = await readFile(path.join(DATA_DIR, name));
      await put(`${PREFIX}/${name}`, body, {
        access: "public",
        token,
        contentType: "application/json",
        addRandomSuffix: false,
        allowOverwrite: true,
        cacheControlMaxAge: 3600,
      });
      done += 1;
      if (done % 100 === 0) console.log(`  ${done}/${names.length}`);
    } catch (err) {
      failed.push(name);
      console.error(`  FAILED ${name}: ${err.message}`);
    }
  }
}
await Promise.all(Array.from({ length: CONCURRENCY }, worker));

console.log(`Uploaded ${done} of ${names.length}.`);
if (failed.length > 0) {
  console.error(`Failed: ${failed.join(", ")}`);
  process.exit(1);
}
