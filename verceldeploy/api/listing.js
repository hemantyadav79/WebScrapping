/** GET /api/listing?id=<hex> — one full record for the detail modal. */
"use strict";
const { env, headers } = require("../lib/supabase");

module.exports = async (req, res) => {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ error: "Method not allowed" });
  }

  // id is an md5 hex hash — sanitise to hex only (defence in depth)
  const id = String((req.query && req.query.id) || "").replace(/[^a-fA-F0-9]/g, "").slice(0, 64);
  if (!id) return res.status(400).json({ error: "Missing or invalid id" });

  let cfg;
  try {
    cfg = env();
  } catch (e) {
    console.error("config error:", e.message);
    return res.status(500).json({ error: "Server is not configured" });
  }

  try {
    const url = cfg.base + "?id=eq." + encodeURIComponent(id) + "&select=*&limit=1";
    const r = await fetch(url, { headers: headers(cfg.key) });
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      console.error("supabase detail error:", r.status, body.slice(0, 300));
      return res.status(502).json({ error: "Could not load listing" });
    }
    const rows = await r.json();
    if (!Array.isArray(rows) || rows.length === 0) {
      return res.status(404).json({ error: "Not found" });
    }
    res.setHeader("Cache-Control", "s-maxage=60, stale-while-revalidate=120");
    return res.status(200).json(rows[0]);
  } catch (e) {
    console.error("listing handler error:", e && e.message ? e.message : e);
    return res.status(502).json({ error: "Could not load listing" });
  }
};
