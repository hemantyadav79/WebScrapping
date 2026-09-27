/** GET /api/facets — distinct values for the filter dropdowns (suburb/status/type/beds).
 *
 * PostgREST has no cheap DISTINCT, so we fetch a capped slice of each single column
 * and dedupe server-side. Correct for the current dataset; heavily cached. At very
 * large scale, replace this with a materialised view / RPC (see README).
 * Degrades gracefully: on any error it returns empty lists so the UI still works.
 */
"use strict";
const { env, headers } = require("../lib/supabase");

const COLS = { suburbs: "suburb", statuses: "status", types: "property_type", beds: "bedrooms" };
const CAP = 5000;

module.exports = async (req, res) => {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ error: "Method not allowed" });
  }

  let cfg;
  try {
    cfg = env();
  } catch (e) {
    console.error("config error:", e.message);
    return res.status(500).json({ error: "Server is not configured" });
  }

  const out = { suburbs: [], statuses: [], types: [], beds: [] };
  try {
    for (const [name, col] of Object.entries(COLS)) {
      try {
        const url = cfg.base + "?select=" + col + "&limit=" + CAP;
        const r = await fetch(url, { headers: headers(cfg.key) });
        if (!r.ok) continue;
        const rows = await r.json();
        const set = new Set();
        for (const row of rows) {
          const v = row && row[col];
          if (v != null) {
            const s = String(v).trim();
            if (s && s !== "N/A" && s !== "0") set.add(s);
          }
        }
        out[name] = Array.from(set).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
      } catch (inner) {
        console.error("facet fetch failed for", col, inner && inner.message);
      }
    }
    res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
    return res.status(200).json(out);
  } catch (e) {
    console.error("facets handler error:", e && e.message ? e.message : e);
    return res.status(200).json(out); // never break the UI over facets
  }
};
