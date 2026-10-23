#!/usr/bin/env python3
"""Convertit les originaux OneDrive en 1080x1920 (plein écran portrait) + filtre Ghibli (AnimeGANv2 face_paint_v2, ONNX).

1. Photos disparues d'OneDrive (absentes du manifest) : déplacées dans backup/ (original + version Ghibli), jamais supprimées.
2. Photos nouvelles : recadrées (visages d'abord, sinon zone la plus détaillée), stylisées, enregistrées dans ghibli/.
3. S'arrête proprement à l'échéance (--deadline, epoch). Reprend où il s'était arrêté au lancement suivant.
"""
import argparse, json, os, random, shutil, sys, time
import cv2, numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps

BASE = os.path.dirname(os.path.abspath(__file__))
S = json.load(open(os.path.join(BASE, "settings.json")))
W, H = S["width"], S["height"]
ORIG, OUT = os.path.join(BASE, "originals"), os.path.join(BASE, "ghibli")
BK_ORIG, BK_OUT = os.path.join(BASE, "backup", "originals"), os.path.join(BASE, "backup", "ghibli")
for d in (ORIG, OUT, BK_ORIG, BK_OUT):
    os.makedirs(d, exist_ok=True)


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "[ghibli]", *a, flush=True)


def free_gb():
    st = os.statvfs(BASE)
    return st.f_bavail * st.f_frsize / 1e9


_face = None


def faces(gray):
    """Visages de face (x, y, w, h) en pixels de l'image d'origine."""
    global _face
    if _face is None:
        _face = False
        for p in (cv2.data.haarcascades if hasattr(cv2, "data") else "", "/usr/share/opencv4/haarcascades/"):
            f = os.path.join(p, "haarcascade_frontalface_default.xml")
            if os.path.exists(f):
                _face = cv2.CascadeClassifier(f)
                break
    if not _face:
        return []
    k = min(1.0, 800 / max(gray.shape))
    g = cv2.resize(gray, None, fx=k, fy=k) if k < 1 else gray
    r = _face.detectMultiScale(g, 1.15, 5, minSize=(max(24, g.shape[1] // 40),) * 2)
    return [(x / k, y / k, w / k, h / k) for x, y, w, h in r]


def focus_point(img):
    """Centre d'intérêt (fx, fy) : visages, sinon barycentre des détails mélangé au centre de l'image."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    fs = faces(gray)
    if fs:
        a = np.array([f[2] * f[3] for f in fs])
        c = np.array([[f[0] + f[2] / 2, f[1] + f[3] / 2] for f in fs])
        return tuple((c * a[:, None]).sum(0) / a.sum())
    g = cv2.resize(gray, (max(w // 8, 1), max(h // 8, 1))).astype(np.float32)
    e = cv2.GaussianBlur(np.abs(cv2.Laplacian(g, cv2.CV_32F)), (0, 0), 3) + 1e-3
    ys, xs = np.mgrid[0:e.shape[0], 0:e.shape[1]]
    cx, cy = (e * xs).sum() / e.sum() * 8, (e * ys).sum() / e.sum() * 8
    return 0.5 * cx + 0.5 * w / 2, 0.5 * cy + 0.5 * h / 2


def crop_fill(img):
    """Recadre au ratio de l'écran autour du centre d'intérêt, puis redimensionne en W x H."""
    h, w = img.shape[:2]
    ratio = W / H
    if w / h > ratio:
        cw, ch = int(round(h * ratio)), h
    else:
        cw, ch = w, int(round(w / ratio))
    fx, fy = focus_point(img)
    x0 = int(min(max(fx - cw / 2, 0), w - cw))
    y0 = int(min(max(fy - ch * 0.42, 0), h - ch))  # visages un peu au-dessus du centre
    c = img[y0:y0 + ch, x0:x0 + cw]
    return cv2.resize(c, (W, H), interpolation=cv2.INTER_AREA if cw > W else cv2.INTER_CUBIC)


def stylize_band(sess, rgb):
    x = (rgb.astype(np.float32) / 127.5 - 1).transpose(2, 0, 1)[None]
    y = sess.run(None, {"in": x})[0][0].transpose(1, 2, 0)
    out = np.clip((y + 1) * 127.5, 0, 255).astype(np.uint8)
    return out if out.shape[:2] == rgb.shape[:2] else cv2.resize(out, (rgb.shape[1], rgb.shape[0]))


def stylize(sess, rgb, bands=1, margin=0):
    """Une seule passe plein écran (pic ~2,3 Go de RAM, MagicMirror arrêté) : pas de coutures. bands>1 = repli par bandes (la normalisation du modèle crée alors des coutures de luminosité)."""
    h = rgb.shape[0]
    out = np.empty_like(rgb)
    edges = [round(i * h / bands) for i in range(bands + 1)]
    for a, b in zip(edges, edges[1:]):
        a0, b0 = max(a - margin, 0), min(b + margin, h)
        res = stylize_band(sess, rgb[a0:b0])
        out[a:b] = res[a - a0:a - a0 + (b - a)]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deadline", type=float, default=0, help="epoch UNIX limite (0 = aucune)")
    ap.add_argument("--max", type=int, default=0, help="nombre max de photos à convertir (test)")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--no-cleanup", action="store_true", help="ne déplace aucune photo en backup (sync OneDrive échouée)")
    a = ap.parse_args()

    m = json.load(open(os.path.join(BASE, "data/manifest.json")))["photos"]
    keys = {p["key"] for p in m}
    stems = lambda d: {os.path.splitext(f)[0] for f in os.listdir(d) if f.endswith(".jpg") and ".tmp" not in f}
    known = stems(ORIG) | stems(OUT)
    gone = sorted(known - keys)
    # Garde-fou : si trop de photos semblent avoir disparu d'un coup (erreur d'API, album inaccessible), on ne déplace rien.
    if a.no_cleanup:
        gone = []
    elif known and len(keys & known) < S["min_keep_ratio"] * len(known):
        log(f"ALERTE : seulement {len(keys & known)}/{len(known)} photos connues sont encore listées -> aucune photo déplacée en backup")
        gone = []
    for k in gone:
        for src, dst in ((os.path.join(ORIG, k + ".jpg"), BK_ORIG), (os.path.join(OUT, k + ".jpg"), BK_OUT)):
            if os.path.exists(src):
                shutil.move(src, os.path.join(dst, k + ".jpg"))
        log("disparue d'OneDrive -> backup :", k)

    todo = [p for p in m if os.path.exists(os.path.join(ORIG, p["key"] + ".jpg")) and not os.path.exists(os.path.join(OUT, p["key"] + ".jpg"))]
    random.Random(42).shuffle(todo)
    log(f"{len(m)} photos OneDrive, {len(m) - len(todo)} déjà converties ou sans original, {len(todo)} à convertir, {len(gone)} déplacées en backup")
    if not todo:
        return 0
    o = ort.SessionOptions()
    o.intra_op_num_threads = a.threads
    o.enable_cpu_mem_arena = False  # rend la mémoire à chaque bande
    o.enable_mem_pattern = False
    sess = ort.InferenceSession(os.path.join(BASE, "models/ghibli_v2.onnx"), o)
    done = 0
    t_all = time.time()
    for p in todo:
        if a.deadline and time.time() > a.deadline - 90:
            log("échéance atteinte, arrêt propre")
            break
        if a.max and done >= a.max:
            break
        if free_gb() < S["min_free_gb"]:
            log(f"disque presque plein ({free_gb():.1f} Go libres) : arrêt")
            return 4
        k = p["key"]
        t = time.time()
        try:
            img = np.array(ImageOps.exif_transpose(Image.open(os.path.join(ORIG, k + ".jpg"))).convert("RGB"))
            crop = crop_fill(img)
            try:
                res = stylize(sess, crop)
            except Exception as e:  # mémoire insuffisante : repli par bandes
                log(f"passe unique impossible ({e!r}), repli sur 4 bandes")
                res = stylize(sess, crop, bands=4, margin=32)
            tmp = os.path.join(OUT, k + ".tmp.jpg")
            Image.fromarray(res).save(tmp, quality=S["jpeg_quality"], optimize=True)
            os.replace(tmp, os.path.join(OUT, k + ".jpg"))
            done += 1
            log(f"{k} ok en {time.time() - t:.0f}s ({done}/{len(todo)})")
        except Exception as e:
            log(f"ÉCHEC {k}: {e!r}")
    log(f"terminé : {done} converties en {(time.time() - t_all) / 60:.1f} min ; {len(todo) - done} restantes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
