#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yerno Millonario - Descargador de capítulos a PDF (Flask + threading)

Ejecutar:  python app.py   ->  http://0.0.0.0:5000
"""
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request, send_from_directory, abort
from fpdf import FPDF

try:
    from pypdf import PdfWriter, PdfReader   # solo para unir PDFs
except ImportError:  # pragma: no cover
    PdfWriter = PdfReader = None

# ----------------------------------------------------------------------------
# Configuración
# ----------------------------------------------------------------------------
START_CHAPTER = int(os.environ.get("YM_START", 6947))
CHAPTERS_PER_PDF = 10
URL_TEMPLATE = "https://xperimentalhamid.com/es/novels/millionaire-son-in-law-novel-chapter-{n}/"

OUTPUT_DIR = os.environ.get("YM_OUTPUT", "/sdcard/yerno millonario")
CACHE_DIR = os.path.join(OUTPUT_DIR, ".cache")          # capítulos descargados aún sin PDF
STATE_FILE = os.path.join(OUTPUT_DIR, "estado.json")

REQUEST_DELAY = 2.0          # segundos entre capítulos (no saturar el servidor)
RETRIES = 3                  # reintentos por error de red
NOT_FOUND_WAIT = 600         # si el capítulo aún no existe, esperar 10 min y volver a probar
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"),
    "Accept-Language": "es-ES,es;q=0.9",
}

# Fuentes TrueType con soporte Unicode (Termux / Android / Linux)
FONT_CANDIDATES = [
    "/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/system/fonts/Roboto-Regular.ttf",
    "/system/fonts/NotoSerif-Regular.ttf",
    "/system/fonts/DroidSans.ttf",
]

os.makedirs(CACHE_DIR, exist_ok=True)

app = Flask(__name__)

# ----------------------------------------------------------------------------
# Estado persistente
# ----------------------------------------------------------------------------
state_lock = threading.Lock()
logs = deque(maxlen=300)
log_counter = 0


def log(msg):
    global log_counter
    with state_lock:
        log_counter += 1
        logs.append({"id": log_counter, "t": datetime.now().strftime("%H:%M:%S"), "msg": msg})
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def default_state():
    return {
        "next_chapter": START_CHAPTER,   # siguiente capítulo a descargar
        "batch_start": START_CHAPTER,    # primer capítulo del bloque actual
        "pdfs": [],                      # PDFs generados
        "merged": [],                    # PDFs unidos
        "updated": None,
    }


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            base = default_state()
            base.update(data)
            return base
        except (json.JSONDecodeError, OSError):
            log("estado.json dañado: se crea uno nuevo")
    return default_state()


def save_state():
    state["updated"] = datetime.now().isoformat(timespec="seconds")
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)   # escritura atómica


state = load_state()

# ----------------------------------------------------------------------------
# Scraping
# ----------------------------------------------------------------------------
session = requests.Session()
session.headers.update(HEADERS)

SKIP_PATTERNS = re.compile(
    r"(cap[ií]tulos? (siguientes?|anteriores?)|suscr[ií]bete|únete a|telegram|"
    r"descarga(r)? (el )?pdf|leer más cap)", re.IGNORECASE)


def fetch_chapter(n):
    """Devuelve dict {title, paragraphs} o None si el capítulo no existe (404)."""
    url = URL_TEMPLATE.format(n=n)
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            content = soup.select_one("div.entry-content")
            if content is None:
                raise ValueError("no se encontró div.entry-content")
            h1 = soup.select_one("h1.entry-title") or soup.find("h1")
            title = h1.get_text(" ", strip=True) if h1 else f"Capítulo {n}"

            for bad in content.select("script, style, ins, iframe, noscript, "
                                      ".wp-block-buttons, .gt-block, .sharedaddy"):
                bad.decompose()

            paragraphs = []
            for p in content.find_all(["p", "h2", "h3"]):
                txt = p.get_text(" ", strip=True)
                if not txt or SKIP_PATTERNS.search(txt):
                    continue
                paragraphs.append(txt)
            if not paragraphs:
                raise ValueError("capítulo sin texto")
            return {"n": n, "title": title, "paragraphs": paragraphs}
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  intento {attempt}/{RETRIES} falló en cap. {n}: {e}")
            if worker.stop_event.wait(5 * attempt):
                break
    raise RuntimeError(f"no se pudo descargar el capítulo {n}: {last_err}")


def cache_path(n):
    return os.path.join(CACHE_DIR, f"{n}.json")


# ----------------------------------------------------------------------------
# PDF
# ----------------------------------------------------------------------------
FONT_PATH = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)

LATIN1_MAP = str.maketrans({
    "“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "‚": "'",
    "—": "-", "–": "-", "…": "...", "\u00a0": " ", "•": "-",
})


class NovelPDF(FPDF):
    def __init__(self, label):
        super().__init__(format="A5")
        self.label = label
        self.set_auto_page_break(auto=True, margin=15)
        self.set_margins(14, 14, 14)
        if FONT_PATH:
            self.add_font("Body", "", FONT_PATH)
            self.font_name = "Body"
        else:
            self.font_name = "Helvetica"

    def txt(self, s):
        if self.font_name == "Helvetica":   # fuentes base: solo latin-1
            return s.translate(LATIN1_MAP).encode("latin-1", "replace").decode("latin-1")
        return s

    def footer(self):
        self.set_y(-10)
        self.set_font(self.font_name, "", 8)
        self.set_text_color(130)
        self.cell(0, 5, self.txt(f"{self.label}  ·  {self.page_no()}"), align="C", new_x="LMARGIN", new_y="NEXT")


def build_pdf(first, last):
    name = f"Capítulos_{first}-{last}.pdf"
    path = os.path.join(OUTPUT_DIR, name)
    pdf = NovelPDF(f"Yerno Millonario {first}-{last}")
    pdf.set_title(f"Yerno Millonario - Capítulos {first}-{last}")

    # Portada
    pdf.add_page()
    pdf.set_font(pdf.font_name, "", 22)
    pdf.ln(60)
    pdf.multi_cell(0, 12, pdf.txt("Yerno Millonario"), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(pdf.font_name, "", 14)
    pdf.multi_cell(0, 9, pdf.txt(f"Capítulos {first} – {last}"), align="C", new_x="LMARGIN", new_y="NEXT")

    for n in range(first, last + 1):
        with open(cache_path(n), "r", encoding="utf-8") as f:
            ch = json.load(f)
        pdf.add_page()
        pdf.set_text_color(0)
        pdf.set_font(pdf.font_name, "", 15)
        pdf.multi_cell(0, 8, pdf.txt(ch["title"]), align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.set_font(pdf.font_name, "", 11)
        for para in ch["paragraphs"]:
            pdf.multi_cell(0, 6, pdf.txt(para), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2.5)

    tmp = path + ".part"
    pdf.output(tmp)
    os.replace(tmp, path)
    return name


# ----------------------------------------------------------------------------
# Hilo de trabajo
# ----------------------------------------------------------------------------
class Worker:
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        self.status = "detenido"      # detenido | descargando | pausando | esperando | error
        self.current = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return False
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return True

    def pause(self):
        if not self.running:
            return False
        self.status = "pausando"
        self.stop_event.set()
        return True

    def run(self):
        self.status = "descargando"
        log(f"▶ Iniciado. Siguiente capítulo: {state['next_chapter']}")
        try:
            while not self.stop_event.is_set():
                # ¿Hay un bloque de 10 completo pendiente de convertir a PDF?
                first = state["batch_start"]
                if state["next_chapter"] - first >= CHAPTERS_PER_PDF:
                    self.make_pdf(first, first + CHAPTERS_PER_PDF - 1)
                    continue

                n = state["next_chapter"]
                self.current = n

                if not os.path.exists(cache_path(n)):
                    log(f"⬇ Descargando capítulo {n}…")
                    ch = fetch_chapter(n)
                    if self.stop_event.is_set():
                        break
                    if ch is None:
                        self.status = "esperando"
                        log(f"⏳ El capítulo {n} aún no está publicado. "
                            f"Reintento en {NOT_FOUND_WAIT // 60} min (o pulsa Pausar).")
                        if self.stop_event.wait(NOT_FOUND_WAIT):
                            break
                        self.status = "descargando"
                        continue
                    with open(cache_path(n), "w", encoding="utf-8") as f:
                        json.dump(ch, f, ensure_ascii=False)
                    log(f"✔ Capítulo {n} guardado ({len(ch['paragraphs'])} párrafos)")
                else:
                    log(f"↺ Capítulo {n} ya estaba en caché")

                with state_lock:
                    state["next_chapter"] = n + 1
                    save_state()

                if self.stop_event.wait(REQUEST_DELAY):
                    break
        except Exception as e:  # noqa: BLE001
            self.status = "error"
            log(f"✖ Error: {e}. Pulsa Reanudar para continuar.")
            self.current = None
            return
        self.status = "detenido"
        self.current = None
        log(f"⏸ Pausado. Se continuará en el capítulo {state['next_chapter']}")

    def make_pdf(self, first, last):
        log(f"📄 Generando PDF {first}-{last}…")
        name = build_pdf(first, last)
        with state_lock:
            state["pdfs"] = [p for p in state["pdfs"] if p["file"] != name]  # sin duplicados
            state["pdfs"].append({"file": name, "first": first, "last": last,
                                  "date": datetime.now().isoformat(timespec="seconds")})
            state["batch_start"] = last + 1
            save_state()
        for k in range(first, last + 1):
            try:
                os.remove(cache_path(k))
            except OSError:
                pass
        log(f"✅ PDF listo: {name}")


worker = Worker()

# ----------------------------------------------------------------------------
# Unir PDFs
# ----------------------------------------------------------------------------
merge_lock = threading.Lock()
merge_status = {"running": False}


def merge_pdfs(items):
    """Une los PDFs (ordenados por capítulo) en un único archivo con marcadores."""
    first, last = items[0]["first"], items[-1]["last"]
    name = f"Yerno_Millonario_{first}-{last}_completo.pdf"
    path = os.path.join(OUTPUT_DIR, name)
    log(f"🔗 Uniendo {len(items)} PDFs ({first}-{last})…")
    writer = PdfWriter()
    for it in items:
        reader = PdfReader(os.path.join(OUTPUT_DIR, it["file"]))
        writer.append(reader, outline_item=f"Capítulos {it['first']}-{it['last']}")
    writer.add_metadata({"/Title": f"Yerno Millonario - Capítulos {first}-{last}"})
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        writer.write(f)
    writer.close()
    os.replace(tmp, path)
    size_mb = os.path.getsize(path) / 1_048_576
    with state_lock:
        state["merged"] = [m for m in state.get("merged", []) if m["file"] != name]
        state["merged"].append({"file": name, "first": first, "last": last, "parts": len(items),
                                "date": datetime.now().isoformat(timespec="seconds")})
        save_state()
    log(f"✅ PDF unido listo: {name} ({size_mb:.1f} MB)")


def merge_thread(items):
    try:
        merge_pdfs(items)
    except Exception as e:  # noqa: BLE001
        log(f"✖ Error al unir PDFs: {e}")
    finally:
        merge_status["running"] = False
        merge_lock.release()

# ----------------------------------------------------------------------------
# Rutas
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    since = request.args.get("since", 0, type=int)
    with state_lock:
        new_logs = [l for l in logs if l["id"] > since]
        first = state["batch_start"]
        done_in_batch = state["next_chapter"] - first
        pdfs = [p for p in state["pdfs"] if os.path.exists(os.path.join(OUTPUT_DIR, p["file"]))]
        merged = [m for m in state.get("merged", [])
                  if os.path.exists(os.path.join(OUTPUT_DIR, m["file"]))]
        return jsonify({
            "status": worker.status if worker.running or worker.status == "error" else "detenido",
            "running": worker.running,
            "current": worker.current,
            "next_chapter": state["next_chapter"],
            "batch_start": first,
            "batch_end": first + CHAPTERS_PER_PDF - 1,
            "batch_done": done_in_batch,
            "batch_size": CHAPTERS_PER_PDF,
            "total_downloaded": sum(p["last"] - p["first"] + 1 for p in state["pdfs"]) + done_in_batch,
            "pdfs": list(reversed(pdfs)),
            "merged": list(reversed(merged)),
            "merging": merge_status["running"],
            "logs": new_logs,
            "output_dir": OUTPUT_DIR,
            "default_start": START_CHAPTER,
        })


@app.route("/api/start", methods=["POST"])
def api_start():
    ok = worker.start()
    return jsonify({"ok": ok, "msg": "iniciado" if ok else "ya está en marcha"})


@app.route("/api/resume", methods=["POST"])
def api_resume():
    return api_start()


@app.route("/api/pause", methods=["POST"])
def api_pause():
    ok = worker.pause()
    return jsonify({"ok": ok, "msg": "pausando…" if ok else "no estaba en marcha"})


@app.route("/api/flush", methods=["POST"])
def api_flush():
    """Genera un PDF parcial con los capítulos descargados del bloque actual."""
    if worker.running:
        return jsonify({"ok": False, "msg": "pausa primero la descarga"}), 409
    first, last = state["batch_start"], state["next_chapter"] - 1
    if last < first:
        return jsonify({"ok": False, "msg": "no hay capítulos pendientes"}), 400
    worker.make_pdf(first, last)
    return jsonify({"ok": True, "msg": f"PDF {first}-{last} generado"})


@app.route("/api/merge", methods=["POST"])
def api_merge():
    """Une todos los PDFs completados (o un rango: {"from": 6947, "to": 7046})."""
    if PdfWriter is None:
        return jsonify({"ok": False, "msg": "falta pypdf: pip install pypdf"}), 500
    data = request.get_json(silent=True) or {}
    lo = data.get("from") or 0
    hi = data.get("to") or 10**9
    with state_lock:
        items = sorted(
            (p for p in state["pdfs"]
             if os.path.exists(os.path.join(OUTPUT_DIR, p["file"]))
             and p["first"] >= int(lo) and p["last"] <= int(hi)),
            key=lambda p: p["first"])
    if len(items) < 2:
        return jsonify({"ok": False, "msg": "se necesitan al menos 2 PDFs completados"}), 400
    if not merge_lock.acquire(blocking=False):
        return jsonify({"ok": False, "msg": "ya se están uniendo PDFs"}), 409
    merge_status["running"] = True
    threading.Thread(target=merge_thread, args=(items,), daemon=True).start()
    return jsonify({"ok": True, "msg": f"uniendo {len(items)} PDFs…"})


@app.route("/api/set_start", methods=["POST"])
def api_set_start():
    """Cambia el capítulo desde el que se continuará (solo en pausa).
    JSON: {"chapter": 7000}  o  {"reset": true} para volver al capítulo inicial."""
    if worker.running:
        return jsonify({"ok": False, "msg": "pausa primero la descarga"}), 409
    data = request.get_json(silent=True) or {}
    if data.get("reset"):
        n = START_CHAPTER
    else:
        try:
            n = int(data.get("chapter"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "msg": "número de capítulo no válido"}), 400
    if not 1 <= n <= 99999:
        return jsonify({"ok": False, "msg": "capítulo fuera de rango"}), 400

    # Los capítulos del bloque pendiente se descartan (los PDFs ya hechos se conservan)
    removed = 0
    for f in os.listdir(CACHE_DIR):
        if f.endswith(".json"):
            try:
                os.remove(os.path.join(CACHE_DIR, f))
                removed += 1
            except OSError:
                pass
    with state_lock:
        state["next_chapter"] = n
        state["batch_start"] = n
        if data.get("reset"):
            state["pdfs"] = [p for p in state["pdfs"]
                             if os.path.exists(os.path.join(OUTPUT_DIR, p["file"]))]
        save_state()
    worker.status = "detenido"
    extra = f" ({removed} capítulos en caché descartados)" if removed else ""
    log(f"⚙ Capítulo inicial cambiado a {n}{extra}")
    return jsonify({"ok": True, "msg": f"se continuará desde el capítulo {n}"})


@app.route("/download/<path:filename>")
def download(filename):
    if not filename.lower().endswith(".pdf") or "/" in filename or "\\" in filename:
        abort(404)
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    log(f"Carpeta de salida: {OUTPUT_DIR}")
    log(f"Fuente PDF: {FONT_PATH or 'Helvetica (sin Unicode completo)'}")
    log(f"Estado cargado: siguiente capítulo {state['next_chapter']}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
import os
from flask import jsonify, request, send_from_directory

# Ajusta DOWNLOAD_FOLDER según cómo lo tengas definido en tu app.py
DOWNLOAD_FOLDER = "downloads"  


@app.route("/delete_pdf/<filename>", methods=["DELETE"])
def delete_pdf(filename):
  try:
    # Previene ataques de traversía de directorios
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(DOWNLOAD_FOLDER, safe_filename)

    if os.path.exists(file_path):
      os.remove(file_path)
      return jsonify(
          {"status": "success", "message": f"{safe_filename} eliminado"}
      )
    else:
      return jsonify(
          {"status": "error", "message": "El archivo no existe"}
      ), 404
  except Exception as e:
    return jsonify({"status": "error", "message": str(e)}), 500
