// Pulse Cron Trigger — Cloudflare Worker
//
// Cloudflare Workers Cron Triggers fire at exactly the configured UTC time,
// with sub-second precision. GitHub Actions' own cron is best-effort and
// frequently fires 30-90 minutes late under load. This Worker bypasses that
// by firing workflow_dispatch directly against the GitHub API.
//
// Schedule: 11:00 UTC daily (7:00am ET in EDT, 6:00am ET in EST).
// Target: azizsunderji/HomeEconomics .github/workflows/pulse-synth.yml
//
// Bindings required (set via Cloudflare Dashboard or wrangler.toml):
//   - GITHUB_TOKEN (secret) — fine-grained PAT with Actions: read/write
//     on azizsunderji/HomeEconomics. NEVER commit this to git.
//   - REPO_OWNER, REPO_NAME, WORKFLOW_FILE (plain vars, see wrangler.toml)
//   - MANUAL_TRIGGER_KEY (secret) — random string for the /trigger HTTP path
//
// Manual trigger for testing:
//   curl -X POST "https://pulse-cron-trigger.<your-subdomain>.workers.dev/?key=<MANUAL_TRIGGER_KEY>"

async function dispatchWorkflow(env) {
  const url = `https://api.github.com/repos/${env.REPO_OWNER}/${env.REPO_NAME}/actions/workflows/${env.WORKFLOW_FILE}/dispatches`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "User-Agent": "pulse-cron-trigger/1.0",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    const msg = `GitHub workflow_dispatch failed: HTTP ${resp.status} ${text.slice(0, 300)}`;
    console.error(msg);
    throw new Error(msg);
  }
  console.log(`workflow_dispatch fired at ${new Date().toISOString()} — HTTP ${resp.status}`);
  return resp.status;
}

export default {
  // Fired by the cron trigger configured in wrangler.toml.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatchWorkflow(env));
  },

  // Manual trigger path for testing — POST /?key=<MANUAL_TRIGGER_KEY>
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("Pulse cron trigger. POST with ?key=... to fire manually.\n", {
        status: 405,
        headers: { "Content-Type": "text/plain" },
      });
    }
    const url = new URL(request.url);
    const provided = url.searchParams.get("key") || "";
    if (!env.MANUAL_TRIGGER_KEY || provided !== env.MANUAL_TRIGGER_KEY) {
      return new Response("Unauthorized\n", { status: 401 });
    }
    try {
      const status = await dispatchWorkflow(env);
      return new Response(`workflow_dispatch fired (HTTP ${status})\n`, { status: 200 });
    } catch (e) {
      return new Response(`Error: ${e.message}\n`, { status: 500 });
    }
  },
};
