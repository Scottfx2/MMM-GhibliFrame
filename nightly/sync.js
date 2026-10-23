// Synchronise les originaux OneDrive (albums de settings.json) vers ./originals
// et écrit data/manifest.json. Réutilise l'authentification de MMM-OneDrive (msal/token.json).
// Ne supprime jamais rien : la gestion des photos disparues est faite par ghibli.py.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const MM = process.env.MM_DIR || path.join(require("node:os").homedir(), "MagicMirror"); // MagicMirror install dir
const MOD = path.join(MM, "modules/MMM-OneDrive");
process.env.NODE_PATH = path.join(MM, "js") + path.delimiter + path.join(MM, "node_modules"); // 'logger' de MagicMirror
require("node:module").Module._initPaths();
const { OneDrivePhotos } = require(path.join(MOD, "lib/OneDrivePhotos.js"));
const BASE = __dirname;
const settings = JSON.parse(fs.readFileSync(path.join(BASE, "settings.json"), "utf8"));
const ORIG = path.join(BASE, "originals");
fs.mkdirSync(ORIG, { recursive: true });
fs.mkdirSync(path.join(BASE, "data"), { recursive: true });
const LIST_ONLY = process.argv.includes("--list-only");

const log = (...a) => console.log(new Date().toISOString(), "[sync]", ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const shortId = (id) => id.replace(/[^A-Za-z0-9]/g, "").slice(-10);

async function download(url, dest) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error("HTTP " + res.status);
      const buf = Buffer.from(await res.arrayBuffer());
      const isJpeg = buf[0] === 0xff && buf[1] === 0xd8;
      const isPng = buf[0] === 0x89 && buf[1] === 0x50;
      if (!isJpeg && !isPng) throw Object.assign(new Error("format non géré (HEIC ?)"), { fatal: true });
      fs.writeFileSync(dest + ".part", buf);
      fs.renameSync(dest + ".part", dest);
      return buf.length;
    } catch (e) {
      if (e.fatal || attempt === 3) throw e;
      await sleep(2000 * attempt);
    }
  }
}

(async () => {
  const od = new OneDrivePhotos({
    debug: false,
    config: { albums: settings.albums, updateInterval: 60000, sort: "old", condition: {}, showWidth: settings.width, showHeight: settings.height },
    authTokenCachePath: path.join(MOD, "msal/token.json"),
  });
  let authError = null;
  od.on("errorMessage", (m) => { authError = m; log("ERREUR auth:", JSON.stringify(m)); });

  const albums = (await od.getAlbums()).filter((a) => settings.albums.includes(a.name));
  if (albums.length !== settings.albums.length) throw new Error("Albums introuvables : trouvés " + albums.map((a) => a.name).join(","));
  const manifest = [];
  for (const album of albums) {
    const list = await od.getImageFromAlbum(album.id, (p) => p.mediaMetadata && !p.mimeType.startsWith("video/"));
    log(`album ${album.name}: ${list.length} photos`);
    for (const p of list) {
      manifest.push({
        key: `${path.parse(p.filename).name}_${shortId(p.id)}`, id: p.id, filename: p.filename, album: album.name,
        date: p.mediaMetadata.dateTimeOriginal || null, width: p.mediaMetadata.width || null, height: p.mediaMetadata.height || null,
        _item: p,
      });
    }
  }
  if (!manifest.length) throw new Error("liste vide, abandon");
  const seen = new Set(), dup = manifest.filter((m) => seen.has(m.key) || !seen.add(m.key));
  if (dup.length) throw new Error("clés en double : " + dup.slice(0, 3).map((d) => d.key));

  let got = 0, failed = 0;
  if (!LIST_ONLY) {
    for (const m of manifest) {
      const dest = path.join(ORIG, m.key + ".jpg");
      if (fs.existsSync(dest)) continue;
      try {
        let item = m._item;
        if (item.baseUrlExpireDateTime && new Date(item.baseUrlExpireDateTime) < new Date(Date.now() + 60000)) item = (await od.refreshItem(item)) || item;
        await download(item.baseUrl, dest);
        got++;
        if (got % 25 === 0) log(`${got} originaux téléchargés...`);
      } catch (e) {
        failed++;
        log(`échec ${m.filename}: ${e.message}`);
      }
    }
  }
  const out = manifest.map(({ _item, ...r }) => r);
  fs.writeFileSync(path.join(BASE, "data/manifest.json.tmp"), JSON.stringify({ generated: new Date().toISOString(), photos: out }, null, 1));
  fs.renameSync(path.join(BASE, "data/manifest.json.tmp"), path.join(BASE, "data/manifest.json"));
  log(`manifest: ${out.length} photos, ${got} téléchargées, ${failed} échecs`);
  process.exit(authError ? 3 : 0);
})().catch((e) => { log("ÉCHEC:", e && e.stack || e); process.exit(2); });
