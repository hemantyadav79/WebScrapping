/** GET /api/listings — paginated, searchable, filterable list (light columns). */
"use strict";
const { env, headers, buildListQuery, parseTotal } = require("../lib/supabase");

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

  try {
    const { qs, page, size } = buildListQuery(req.query || {});
    const url = cfg.base + "?" + qs;

    const r = await fetch(url, { headers: headers(cfg.key, { Prefer: "count=exact" }) });
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      console.error("supabase list error:", r.status, body.slice(0, 300));
      return res.status(502).json({ error: "Could not load listings" });
    }

    const rows = await r.json();
    const total = parseTotal(r.headers.get("content-range"));

    // live, but a short CDN cache protects Supabase + keeps the site fast
    res.setHeader("Cache-Control", "s-maxage=30, stale-while-revalidate=60");
    return res.status(200).json({
      rows: Array.isArray(rows) ? rows : [],
      page,
      pageSize: size,
      total,
      hasMore: total == null ? (Array.isArray(rows) && rows.length === size) : page * size < total,
    });
  } catch (e) {
    console.error("listings handler error:", e && e.message ? e.message : e);
    return res.status(502).json({ error: "Could not load listings" });
  }
};
