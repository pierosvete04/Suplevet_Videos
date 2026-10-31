"""La cuenta: entrar con Google, quedarse con la sesión abierta y ver en qué dispositivos se abrió.

Es el mismo archivo en las tres apps (Sala de edición, Sala de diseño y Publicador): si se cambia en una, se copia
igual a las otras dos. Todo lo de la cuenta vive en la PC en %LOCALAPPDATA%/Salas (una sola sesión para las tres):
  rclone.conf      la llave de Google (no sale nunca de la PC)
  drive.json       el proyecto (link de la carpeta de Drive) y quién entró (correo, nombre, foto)
  dispositivo.json el id de esta PC

El candado de verdad es Drive: solo entra quien tenga compartida la carpeta del proyecto. Lo que se ve en
«Dispositivos y seguridad» vive en Drive, en _SISTEMA/dispositivos/<correo>/<id de la PC>.json, y cada app lo
actualiza al abrir y cada 10 minutos. «Cerrar sesión en esa PC» deja una marca que esa PC lee en su siguiente
latido y entonces cierra la sesión sola.

La primera vez se entra con Google sin link y se elige la carpeta del proyecto (una propia, una compartida o una
nueva): ver «Primera vez» abajo. Cada app agrega lo suyo después de elegir (por ejemplo, crear su marca base).
"""
import io
import json
import os
import platform
import re
import socket
import subprocess
import threading
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

SUITE = Path(os.environ.get("SALAS_SUITE") or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Salas")
SUITE.mkdir(parents=True, exist_ok=True)
REMOTO = "proyecto"
CONF = SUITE / "rclone.conf"
INFO = SUITE / "drive.json"
DISPOSITIVO = SUITE / "dispositivo.json"
CARPETA = "_SISTEMA/dispositivos"
DIAS_NUEVO = 7
_app = {"nombre": "", "version": ""}
_candado = threading.Lock()
_ultimo_latido = {"t": 0}
PROCESOS = set()


def iniciar(app, version):
    """Cada app se presenta al arrancar: así el registro de dispositivos dice qué apps usa cada PC."""
    _app.update(nombre=app, version=version)
    traer_sesion_vieja()


# ---------- rclone ----------
def rclone():
    destino = SUITE / "herramientas" / "rclone.exe"
    otras = [Path(os.environ.get("LOCALAPPDATA", "")) / "SalaSuplevet" / "datos" / "herramientas" / "rclone.exe"]
    for c in [destino, *otras]:
        if c.exists():
            return c
    destino.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request("https://downloads.rclone.org/rclone-current-windows-amd64.zip", headers={"User-Agent": "Mozilla/5.0 Salas"})
    with urllib.request.urlopen(req, timeout=300) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    destino.write_bytes(z.read(next(n for n in z.namelist() if n.endswith("rclone.exe"))))
    return destino


def rc(*args, timeout=None, entrada=None):
    cmd = [str(rclone()), *map(str, args), "--config", str(CONF)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE if entrada is not None else None,
                         text=True, encoding="utf-8", errors="replace", creationflags=0x08000000 if os.name == "nt" else 0)
    PROCESOS.add(p)
    try:
        out, err = p.communicate(entrada, timeout=timeout)
    except subprocess.TimeoutExpired:  # rclone no se queda vivo esperando (ocuparía el puerto de Google)
        p.kill()
        p.communicate()
        raise RuntimeError(f"Drive no respondió en {timeout} s. Vuelve a intentar.")
    finally:
        PROCESOS.discard(p)
    if p.returncode != 0:
        raise RuntimeError("Drive: " + ((err or "").strip().splitlines() or ["error desconocido"])[-1])
    return out


# ---------- Entrar con Google ----------
# rclone abre Google en el navegador y espera a que se acepte. Si el navegador no se abrió (o se abrió detrás, o se
# cerró la pestaña), la pantalla de entrar muestra el link que escribió rclone para abrirlo de nuevo. Un ingreso que
# quedó esperando se corta antes de empezar otro: si no, ocupa el puerto de Google y el nuevo no puede abrirse.
_google = {"link": "", "proc": None, "cancelado": False}


def cancelar_google():
    p = _google["proc"]
    if p and p.poll() is None:
        _google["cancelado"] = True
        p.kill()


def abrir_google():
    """Vuelve a abrir en el navegador la página de Google del ingreso que está esperando."""
    if not _google["link"]:
        raise RuntimeError("Google todavía no está listo. Espera unos segundos.")
    import webbrowser
    webbrowser.open(_google["link"])


def _rc_google(*args, timeout=600):
    cancelar_google()
    cmd = [str(rclone()), *map(str, args), "--config", str(CONF)]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         errors="replace", creationflags=0x08000000 if os.name == "nt" else 0)
    _google.update(link="", proc=p, cancelado=False)
    PROCESOS.add(p)
    vencido = threading.Event()
    reloj = threading.Timer(timeout, lambda: (vencido.set(), p.kill()))
    reloj.start()
    lineas = []
    try:
        for linea in p.stderr:  # «…go to the following link: http://127.0.0.1:53682/auth?state=…» y luego espera
            m = re.search(r"http://127\.0\.0\.1:\d+/auth\?state=[\w-]+", linea)
            if m:
                _google["link"] = m.group(0)
            lineas.append(linea.strip())
        p.wait()
    finally:
        reloj.cancel()
        PROCESOS.discard(p)
        if _google["proc"] is p:
            _google.update(link="", proc=None)
    if p.returncode != 0:
        if _google["cancelado"]:
            raise RuntimeError("Se canceló la entrada con Google.")
        if vencido.is_set():
            raise RuntimeError(f"Pasaron {timeout // 60} minutos sin aceptar en Google. Vuelve a intentar.")
        if any("bind" in x and "53682" in x for x in lineas):
            raise RuntimeError("Otra app (Sala de edición, Sala de diseño o Publicador) está esperando a Google. "
                               "Termina de entrar allí o ciérrala, y vuelve a intentar.")
        raise RuntimeError("Google: " + ([x for x in lineas if x] or ["error desconocido"])[-1])


# ---------- Estado de la sesión ----------
def _leer(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _guardar(p, datos):
    Path(p).write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def _llave():
    try:
        texto = CONF.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return ""
    m = re.search(rf"^\[{REMOTO}\]\n(.*?)(?=^\[|\Z)", texto, re.S | re.M)
    return m.group(1) if m else ""


def estado():
    i = _leer(INFO)
    con_llave = "token" in _llave()
    return {"conectado": bool(i.get("activo")) and con_llave, "email": i.get("email", ""), "nombre": i.get("nombre", ""),
            "foto": i.get("foto", ""), "link": i.get("link", ""), "carpeta_id": i.get("carpeta_id", ""),
            "carpeta_nombre": i.get("carpeta_nombre", ""), "con_llave": con_llave, "sin_carpeta": bool(i.get("sin_carpeta")),
            "dispositivo": dispositivo()["nombre"], "cerrada_desde_otra_pc": bool(i.get("cerrada_desde_otra_pc"))}


def carpeta_id(link):
    m = re.search(r"/folders/([\w-]{10,})", link) or re.search(r"[?&]id=([\w-]{10,})", link) or re.fullmatch(r"([\w-]{20,})", link.strip())
    if not m:
        raise ValueError("No reconozco el link. Copia el enlace de la carpeta del proyecto desde Drive (Compartir → Copiar enlace).")
    return m.group(1)


def traer_sesion_vieja():
    """Si esta PC ya estaba conectada con la Sala de edición (antes de la cuenta común), se reusa esa llave.
    Solo la primera vez: si alguien cerró sesión, no se vuelve a entrar solo."""
    if "token" in _llave() or INFO.exists():
        return
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "SalaSuplevet" / "datos"
    try:
        vieja = (base / "rclone.conf").read_text(encoding="utf-8")
        cfg = json.loads((base / "config.local.json").read_text(encoding="utf-8")).get("drive", {})
    except (FileNotFoundError, ValueError):
        return
    if "token" not in vieja or not cfg.get("carpeta_id") or not cfg.get("activo"):
        return
    seccion = re.search(r"^\[[^\]]+\]\n.*?(?=^\[|\Z)", vieja, re.S | re.M)
    if not seccion:
        return
    CONF.write_text(re.sub(r"^\[[^\]]+\]", f"[{REMOTO}]", seccion.group(0).strip(), count=1) + "\n", encoding="utf-8")
    info = {"activo": True, "link": cfg.get("link", ""), "carpeta_id": cfg["carpeta_id"], "entro": time.strftime("%Y-%m-%d %H:%M:%S")}
    cid = re.search(r"^client_id\s*=\s*(\S+)", seccion.group(0), re.M)
    secreto = re.search(r"^client_secret\s*=\s*(\S+)", seccion.group(0), re.M)
    if cid and secreto:  # el cliente de Google del equipo: se reusa para las próximas entradas
        info["cliente"] = {"id": cid.group(1), "secreto": secreto.group(1)}
    _guardar(INFO, info)
    try:
        _quien()
    except Exception:  # sin internet: se averigua después
        pass


_token = {"valor": "", "hasta": 0}


def token():
    """Llave de acceso a la API de Drive (la misma sesión de rclone), renovada si está por vencer."""
    if _token["valor"] and time.time() < _token["hasta"]:
        return _token["valor"]
    rc("lsjson", f"{REMOTO}:", "--max-depth", "1", "--dirs-only", timeout=60)  # rclone renueva la llave si venció
    m = re.search(r"^token\s*=\s*(\{.*\})\s*$", _llave(), re.M)
    if not m:
        raise RuntimeError("Google no devolvió la llave")
    _token.update(valor=json.loads(m.group(1))["access_token"], hasta=time.time() + 600)
    return _token["valor"]


def api_drive(ruta):
    """GET a la API de Drive v3 con la sesión de la cuenta (ej.: «files/<id>?fields=thumbnailLink»)."""
    req = urllib.request.Request("https://www.googleapis.com/drive/v3/" + ruta, headers={"Authorization": f"Bearer {token()}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _quien():
    """Correo, nombre y foto de la cuenta de Google con la que se entró (API de Drive, con la misma llave)."""
    _token["hasta"] = 0
    token = globals()["token"]()
    req = urllib.request.Request("https://www.googleapis.com/drive/v3/about?fields=user", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        u = json.loads(r.read())["user"]
    i = _leer(INFO)
    i.update(email=u.get("emailAddress", ""), nombre=u.get("displayName", ""), foto=u.get("photoLink", ""))
    i.pop("cerrada_desde_otra_pc", None)
    _guardar(INFO, i)
    return i


def entrar(link):
    """Abre Google en el navegador para entrar (una sola vez por PC) y deja la sesión abierta en las tres apps."""
    cid = carpeta_id(link)
    cancelar_google()  # antes del candado: un ingreso que quedó esperando lo tiene tomado
    with _candado:
        if CONF.exists():
            try:
                rc("config", "delete", REMOTO)
            except RuntimeError:
                pass
        cliente = _leer(INFO).get("cliente") or {}
        extra = [f"client_id={cliente['id']}", f"client_secret={cliente['secreto']}"] if cliente.get("id") else []
        _rc_google("config", "create", REMOTO, "drive", "scope=drive", f"root_folder_id={cid}", *extra)
        try:
            rc("lsjson", f"{REMOTO}:", "--max-depth", "1", timeout=60)
        except RuntimeError:
            raise RuntimeError("Esa cuenta no tiene acceso a la carpeta del proyecto. Pídele al dueño que la comparta con tu correo.")
        info = {**_leer(INFO), "activo": True, "link": link, "carpeta_id": cid, "entro": time.strftime("%Y-%m-%d %H:%M:%S")}
        for k in ("sin_carpeta", "anterior", "carpeta_nombre"):
            info.pop(k, None)
        _guardar(INFO, info)
        _quien_si_se_puede()
    latido(forzar=True)
    return estado()


def cerrar_sesion(marcar_en_drive=True):
    """Borra la llave de esta PC: las tres apps vuelven a la pantalla de entrar."""
    if marcar_en_drive:  # en Drive queda «sesión cerrada» para todas las apps de esta PC
        try:
            i, did = _leer(INFO), dispositivo()["id"]
            carpeta = _carpeta_email(i["email"])
            for a in json.loads(rc("lsjson", f"{REMOTO}:{carpeta}", "--files-only", "--include", f"{did}__*.json", timeout=60) or "[]"):
                if a["Name"].endswith("__cerrar.json"):
                    continue
                reg = _leer_remoto(f"{carpeta}/{a['Name']}")
                if reg:
                    reg["estado"] = "cerrada"
                    _escribir_remoto(f"{carpeta}/{a['Name']}", reg)
        except Exception:
            pass
    with _candado:
        try:
            rc("config", "delete", REMOTO)
        except Exception:
            pass
        i = _leer(INFO)
        _guardar(INFO, {k: i[k] for k in ("link", "carpeta_id", "carpeta_nombre", "cliente", "cerrada_desde_otra_pc") if k in i})


# ---------- Primera vez: entrar con Google y elegir (o crear) la carpeta del proyecto ----------
# Para quien recién instala una app: entra con su cuenta, ve sus carpetas y las compartidas con ella, y elige una o
# crea la suya. Mientras no elige, las apps no usan la cuenta (sin carpeta no hay dónde trabajar).

def entrar_sin_carpeta(client_id="", client_secret=""):
    """Abre Google en el navegador para entrar. client_id/secret: las del proyecto de Google Cloud del equipo (opcional)."""
    cancelar_google()
    with _candado:
        info = _leer(INFO)
        if client_id.strip():
            info["cliente"] = {"id": client_id.strip(), "secreto": client_secret.strip()}
        if CONF.exists():
            try:
                rc("config", "delete", REMOTO)
            except RuntimeError:
                pass
        cliente = info.get("cliente") or {}
        extra = [f"client_id={cliente['id']}", f"client_secret={cliente['secreto']}"] if cliente.get("id") else []
        _rc_google("config", "create", REMOTO, "drive", "scope=drive", *extra)
        if info.get("carpeta_id"):  # la carpeta de antes: se ofrece primero al elegir
            info["anterior"] = {"id": info["carpeta_id"], "nombre": info.get("carpeta_nombre", "")}
        info.update(activo=False, sin_carpeta=True, link="", carpeta_id="", carpeta_nombre="",
                    entro=time.strftime("%Y-%m-%d %H:%M:%S"))
        _guardar(INFO, info)
    _quien_si_se_puede()
    return estado()


def _quien_si_se_puede():
    """Google ya aceptó: si no se puede leer el correo (internet que se corta), la entrada vale igual y el latido lo trae."""
    try:
        _quien()
    except Exception as e:  # noqa: BLE001
        print("Cuenta: no pude leer quién entró:", e, flush=True)


_SIN_RAIZ = f"{REMOTO},root_folder_id='':"  # «Mi unidad» entera, aunque la conexión ya apunte a una carpeta


def carpetas_para_elegir():
    """{mias, compartidas, anterior, cuenta, con_llave}: las carpetas de «Mi unidad» y las compartidas con esta cuenta."""
    con_llave = "token" in _llave()

    def lista(*extra):
        if not con_llave:
            return []
        try:
            datos = json.loads(rc("lsjson", _SIN_RAIZ, "--dirs-only", "--max-depth", "1", *extra, timeout=120) or "[]")
        except RuntimeError:
            return []
        return sorted(({"nombre": x["Name"], "id": x["ID"]} for x in datos if x.get("ID") and not x["Name"].startswith(".")),
                      key=lambda x: x["nombre"].lower())
    return {"mias": lista(), "compartidas": lista("--drive-shared-with-me"), "anterior": _leer(INFO).get("anterior") or {},
            "cuenta": estado(), "con_llave": con_llave}


def crear_carpeta(nombre="EDICIÓN DE VIDEOS"):
    """Crea la carpeta del proyecto en «Mi unidad» (si ya existe una con ese nombre, la usa). Devuelve (id, nombre)."""
    nombre = " ".join(str(nombre or "").split())[:80] or "EDICIÓN DE VIDEOS"
    if any(c in nombre for c in '\\/:*?"<>|'):
        raise ValueError('El nombre de la carpeta no puede llevar / \\ : * ? " < > |')
    for sub in ("", "/_MARCA", "/_SISTEMA"):  # lo de base que leen las tres apps
        rc("mkdir", f"{_SIN_RAIZ}{nombre}{sub}", timeout=120)
    d = next((x for x in carpetas_para_elegir()["mias"] if x["nombre"] == nombre), None)
    if not d:
        raise RuntimeError("Se creó la carpeta pero Drive no la muestra todavía: vuelve a intentar en un momento")
    return d["id"], nombre


def usar_carpeta(cid, nombre=""):
    """Deja la sesión trabajando dentro de esa carpeta y revisa si se puede escribir en ella. Devuelve {editor, link, nombre}."""
    if not re.fullmatch(r"[\w-]{10,}", cid or ""):
        raise ValueError("Esa carpeta no es válida")
    with _candado:
        # se cambia solo la línea root_folder_id de la conexión (sin librerías que el .exe podría no traer)
        texto = CONF.read_text(encoding="utf-8") if CONF.exists() else ""
        m = re.search(rf"^\[{REMOTO}\]\n(.*?)(?=^\[|\Z)", texto, re.S | re.M)
        if not m:
            raise RuntimeError("Primero entra con tu cuenta de Google")
        seccion = m.group(1)
        if re.search(r"^root_folder_id\s*=.*$", seccion, re.M):
            seccion = re.sub(r"^root_folder_id\s*=.*$", f"root_folder_id = {cid}", seccion, flags=re.M)
        else:
            seccion = f"root_folder_id = {cid}\n" + seccion
        CONF.write_text(texto[:m.start(1)] + seccion + texto[m.end(1):], encoding="utf-8")
    try:
        rc("lsjson", f"{REMOTO}:", "--max-depth", "1", timeout=60)
    except RuntimeError:
        raise RuntimeError("Tu cuenta no puede abrir esa carpeta. Pide a quien es dueño que te la comparta como Editor.")
    if not nombre:
        try:
            nombre = api_drive(f"files/{cid}?fields=name&supportsAllDrives=true").get("name", "")
        except Exception:  # noqa: BLE001  (el nombre es solo para mostrar)
            pass
    # ¿se puede escribir? (con permiso de solo lectura, Drive rechaza lo que las apps suben)
    editor = True
    prueba = f"_SISTEMA/.prueba_{int(time.time())}.txt"
    try:
        rc("rcat", f"{REMOTO}:{prueba}", timeout=60, entrada="prueba de escritura")
        rc("deletefile", f"{REMOTO}:{prueba}", timeout=60)
    except RuntimeError:
        editor = False
    link = f"https://drive.google.com/drive/folders/{cid}"
    with _candado:
        info = _leer(INFO)
        info.update(activo=True, link=link, carpeta_id=cid, carpeta_nombre=nombre)
        for k in ("sin_carpeta", "anterior", "cerrada_desde_otra_pc"):
            info.pop(k, None)
        _guardar(INFO, info)
    latido(forzar=True)
    return {"editor": editor, "link": link, "nombre": nombre}


# ---------- La marca, la primera vez ----------
# Después de elegir la carpeta, si es nueva o todavía no tiene marca, la pantalla pide lo básico de la marca. Se guarda
# directo en Drive (_MARCA/marca.json y _MARCA/perfil.json, los que leen las tres apps) y cada app lo baja después:
# el servidor ve marca_guardada=True en la respuesta de primera().
_MARCA = "_MARCA"
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def marca_registro():
    """Lo que ya hay de la marca en la carpeta, para llenar el formulario. falta=True si hay que pedirla."""
    m, p = _leer_remoto(f"{_MARCA}/marca.json"), _leer_remoto(f"{_MARCA}/perfil.json")
    colores = {c.get("uso"): c.get("hex") for c in m.get("colores") or [] if isinstance(c, dict)}
    return {"falta": not m.get("nombre") or not (m.get("logos") or p.get("que_vende")),
            "nombre": m.get("nombre") or p.get("nombre") or "", "que_vende": p.get("que_vende", ""), "publico": p.get("publico", ""),
            "principal": colores.get("principal") or colores.get("acento") or "", "secundario": colores.get("secundario") or "",
            "contacto": (m.get("cta") or {}).get("texto", "")}


def registrar_marca(d):
    """Guarda en Drive lo básico de la marca sin pisar lo demás (logos, tipografías, reglas que ya tenga)."""
    texto = lambda k, n: " ".join(str(d.get(k) or "").split())[:n]  # noqa: E731
    nombre = texto("nombre", 60)
    if not nombre:
        raise ValueError("Escribe el nombre de tu marca")
    color = lambda k, sino: d[k].upper() if _HEX.fullmatch(str(d.get(k) or "")) else sino  # noqa: E731
    m, p = _leer_remoto(f"{_MARCA}/marca.json"), _leer_remoto(f"{_MARCA}/perfil.json")
    otros = [c for c in m.get("colores") or [] if isinstance(c, dict) and c.get("uso") not in ("principal", "secundario", "acento")]
    if not any(c.get("uso") == "texto" for c in otros):
        otros.append({"nombre": "Tinta", "hex": "#1F2933", "uso": "texto"})
    if not any(c.get("uso") == "fondo" for c in otros):
        otros.append({"nombre": "Claro", "hex": "#F4F4F5", "uso": "fondo"})
    m.update(nombre=nombre, colores=[{"nombre": "Principal", "hex": color("principal", "#2563EB"), "uso": "principal"},
                                     {"nombre": "Secundario", "hex": color("secundario", "#F59E0B"), "uso": "secundario"}, *otros],
             cambio=time.time())
    m.setdefault("texto_oscuro", "#1F2933")
    m.setdefault("texto_claro", "#FFFFFF")
    if texto("contacto", 120):  # cómo te compran: el cierre de los carruseles y videos
        cta = m.get("cta") or {}
        m["cta"] = {"titulo": cta.get("titulo") or "¿Te interesa?", "texto": texto("contacto", 120), "boton": cta.get("boton") or "Escríbenos"}
    p.update(nombre=nombre, que_vende=texto("que_vende", 300), publico=texto("publico", 300))
    if p["publico"] and not p.get("publicos"):
        p["publicos"] = [p["publico"][:80]]
    _escribir_remoto(f"{_MARCA}/marca.json", m)
    _escribir_remoto(f"{_MARCA}/perfil.json", p)
    return marca_registro()


def primera(b):
    """Lo que piden las pantallas de entrar y elegir carpeta (cuenta.js): {accion: entrar|crear|usar|link, ...}.
    Con «crear» devuelve también nueva=True y el nombre de la marca, para que cada app prepare lo suyo.
    Mientras se espera a Google: google (el link para abrirlo a mano), abrir_google y cancelar_google.
    La marca: marca (lo que ya hay) y marca_guardar (devuelve marca_guardada=True para que la app la baje)."""
    acc = b.get("accion")
    if acc == "marca":
        return {"marca": marca_registro()}
    if acc == "marca_guardar":
        return {"marca": registrar_marca(b.get("marca") or {}), "marca_guardada": True}
    if acc == "google":
        return {"link_google": _google["link"]}
    if acc == "abrir_google":
        abrir_google()
        return {}
    if acc == "cancelar_google":
        cancelar_google()
        return {}
    if acc == "entrar":
        return {"cuenta": entrar_sin_carpeta(str(b.get("client_id") or ""), str(b.get("client_secret") or ""))}
    if acc == "crear":
        cid, nombre = crear_carpeta(str(b.get("nombre") or ""))
        return {**usar_carpeta(cid, nombre), "nueva": True, "marca": " ".join(str(b.get("marca") or "").split())[:60]}
    if acc == "usar":
        return usar_carpeta(str(b.get("id") or ""), str(b.get("nombre") or ""))
    if acc == "link":
        return usar_carpeta(carpeta_id(str(b.get("link") or "")), "")
    raise ValueError("Acción no válida")


# ---------- Dispositivos ----------
def dispositivo():
    d = _leer(DISPOSITIVO)
    if not d.get("id"):
        d = {"id": uuid.uuid4().hex[:12], "creado": time.strftime("%Y-%m-%d %H:%M")}
        _guardar(DISPOSITIVO, d)
    d["nombre"] = socket.gethostname()
    return d


def _slug(texto):
    return re.sub(r"[^a-z0-9]+", "-", texto.lower().replace("ñ", "n").replace("é", "e").replace("ó", "o").replace("í", "i")).strip("-") or "app"


def _carpeta_email(email):
    return f"{CARPETA}/{email}"


def _ruta(email, did, app=None):
    """Cada app escribe solo su archivo: <PC>__<app>.json. El pedido de cierre es <PC>__cerrar.json.
    Así nunca dos programas escriben el mismo archivo (Drive permitiría dos archivos con el mismo nombre)."""
    return f"{_carpeta_email(email)}/{did}__{_slug(app or _app['nombre'])}.json"


def _leer_remoto(ruta):
    try:
        return json.loads(rc("cat", f"{REMOTO}:{ruta}", timeout=30) or "{}")
    except (RuntimeError, ValueError):
        return {}


def _escribir_remoto(ruta, datos):
    rc("rcat", f"{REMOTO}:{ruta}", timeout=60, entrada=json.dumps(datos, ensure_ascii=False, indent=1))


def _anotar(cerrada=False):
    """Anota esta PC con esta app. Devuelve True si otra PC pidió cerrar la sesión de esta."""
    i, d = _leer(INFO), dispositivo()
    if not i.get("email"):
        return False
    ruta = _ruta(i["email"], d["id"])
    previo = _leer_remoto(ruta)
    ahora = time.strftime("%Y-%m-%d %H:%M")
    _escribir_remoto(ruta, {"id": d["id"], "equipo": d["nombre"], "usuario_windows": os.environ.get("USERNAME", ""),
                            "sistema": f"{platform.system()} {platform.release()}", "app": _app["nombre"], "version": _app["version"],
                            "primer_acceso": previo.get("primer_acceso", ahora), "ultimo_acceso": ahora,
                            "estado": "cerrada" if cerrada else "abierta"})
    pedido = _leer_remoto(_ruta(i["email"], d["id"], "cerrar"))
    return bool(pedido) and pedido.get("pedido", "") > i.get("entro", "")  # un pedido viejo no cierra una entrada nueva


def latido(forzar=False):
    """Al abrir y cada 10 minutos: se anota esta PC y se mira si otra PC pidió cerrar esta sesión."""
    if not estado()["conectado"] or (not forzar and time.time() - _ultimo_latido["t"] < 600):
        return
    _ultimo_latido["t"] = time.time()

    def tarea():
        try:
            if not _leer(INFO).get("email"):
                _quien()
            if _anotar():
                cerrar_sesion(marcar_en_drive=True)
                i = _leer(INFO)
                i["cerrada_desde_otra_pc"] = True
                _guardar(INFO, i)
        except Exception as e:  # sin internet: se intenta en el próximo latido
            print("Cuenta:", e, flush=True)
    threading.Thread(target=tarea, daemon=True).start()


def dispositivos():
    """Las PCs donde se abrió esta cuenta (con las apps de cada una), de la más reciente a la más vieja."""
    i, yo = _leer(INFO), dispositivo()["id"]
    if not i.get("email"):
        return []
    carpeta = _carpeta_email(i["email"])
    try:
        rc("dedupe", "--dedupe-mode", "newest", f"{REMOTO}:{carpeta}", timeout=60)  # por si dos apps escribieron a la vez
    except RuntimeError:
        pass
    try:
        archivos = json.loads(rc("lsjson", f"{REMOTO}:{carpeta}", "--files-only", timeout=60) or "[]")
    except RuntimeError:
        return []
    pcs, cierres = {}, {}
    for a in archivos:
        reg = _leer_remoto(f"{carpeta}/{a['Name']}")
        did = a["Name"].split("__")[0]
        if not reg:
            continue
        if a["Name"].endswith("__cerrar.json"):
            cierres[did] = reg
            continue
        pc = pcs.setdefault(did, {"id": did, "equipo": reg.get("equipo"), "usuario_windows": reg.get("usuario_windows"),
                                  "sistema": reg.get("sistema"), "apps": {}, "primer_acceso": reg.get("primer_acceso", ""),
                                  "ultimo_acceso": "", "estado": "cerrada"})
        pc["apps"][reg.get("app") or "?"] = {"version": reg.get("version", ""), "ultimo": reg.get("ultimo_acceso", ""), "estado": reg.get("estado")}
        pc["primer_acceso"] = min(pc["primer_acceso"] or "9", reg.get("primer_acceso", "9"))
        pc["ultimo_acceso"] = max(pc["ultimo_acceso"], reg.get("ultimo_acceso", ""))
        if reg.get("estado") == "abierta":
            pc["estado"] = "abierta"
    limite = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - DIAS_NUEVO * 86400))
    for did, pc in pcs.items():
        pc["esta_pc"] = did == yo
        pc["nuevo"] = pc["primer_acceso"] >= limite
        pc["cerrar"] = did in cierres and pc["estado"] == "abierta"
    return sorted(pcs.values(), key=lambda r: r.get("ultimo_acceso", ""), reverse=True)


def cerrar_en(did):
    """Pide cerrar la sesión de otra PC: esa PC la cierra sola en su siguiente latido (máximo 10 minutos)."""
    i = _leer(INFO)
    if not re.fullmatch(r"[0-9a-f]{6,32}", did or ""):
        raise ValueError("Ese dispositivo no existe")
    _escribir_remoto(_ruta(i["email"], did, "cerrar"), {"pedido": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                        "por": dispositivo()["nombre"]})


def cerrar_en_todas_menos_esta():
    yo = dispositivo()["id"]
    n = 0
    for d in dispositivos():
        if d.get("id") != yo and d.get("estado") != "cerrada":
            cerrar_en(d["id"])
            n += 1
    return n
