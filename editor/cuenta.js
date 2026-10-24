/* La cuenta en pantalla: entrar con Google, elegir la carpeta del proyecto, tu cuenta arriba y «Dispositivos y seguridad».
   Es el mismo archivo en las tres apps (junto con cuenta.py). Uso:
     await Cuenta.iniciar({ app: "Sala de diseño", letra: "D", fondo: "linear-gradient(...)", lugar: elementoDeLaCabeza,
                            alElegirCarpeta: res => {...} });   // opcional: lo de la app después de elegir carpeta
   La promesa se cumple cuando hay sesión; si se cierra (aquí o desde otra PC) vuelve la pantalla de entrar.
   Cada servidor atiende GET /api/cuenta/carpetas (cuenta.carpetas_para_elegir) y POST /api/cuenta/primera (cuenta.primera). */
(() => {
  const css = `
  .cta-velo{position:fixed;inset:0;z-index:9999;display:grid;place-items:center;background:var(--bg,#04060b);color:var(--ink,#edf3ff);font:14px/1.5 "Segoe UI",system-ui,sans-serif}
  .cta-caja{width:min(440px,92vw);text-align:center}
  .cta-logo{width:76px;height:76px;border-radius:20px;display:grid;place-items:center;margin:0 auto 18px;color:#fff;font:800 38px "Segoe UI",system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.35)}
  .cta-caja h1{font:700 24px "Segoe UI",system-ui,sans-serif;margin:0 0 6px}
  .cta-caja p{color:var(--muted,#8494b4);margin:0 0 18px}
  .cta-caja input{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:10px;border:1px solid var(--line2,#24365a);background:var(--bg2,#070b14);color:inherit;font:inherit;margin-bottom:10px}
  .cta-google{width:100%;display:flex;align-items:center;justify-content:center;gap:10px;padding:11px;border-radius:10px;border:1px solid #dadce0;background:#fff;color:#1f1f1f;font:600 15px "Segoe UI",system-ui,sans-serif;cursor:pointer}
  .cta-google:disabled{opacity:.6;cursor:default}
  .cta-nota{min-height:22px;margin-top:12px;color:var(--muted,#8494b4)}
  .cta-nota.err{color:var(--err,#ff6b7d)}
  .cta-chip{display:flex;align-items:center;gap:8px;padding:3px 10px 3px 3px;border-radius:99px;border:1px solid var(--line2,#24365a);background:var(--surface2,#101829);color:inherit;cursor:pointer;font:600 12px "Segoe UI",system-ui,sans-serif;max-width:260px}
  .cta-chip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .cta-av{width:26px;height:26px;border-radius:50%;flex:none;display:grid;place-items:center;background:#3d8bff;color:#fff;font:700 12px "Segoe UI";object-fit:cover}
  .cta-dlg{width:min(640px,94vw);max-height:86vh;overflow:auto;background:var(--surface,#0b111e);color:var(--ink,#edf3ff);border:1px solid var(--line2,#24365a);border-radius:16px;padding:20px;font:13px/1.5 "Segoe UI",system-ui,sans-serif}
  .cta-dlg::backdrop{background:rgba(0,0,0,.55)}
  .cta-dlg h2{margin:0 0 4px;font:700 18px "Segoe UI"}
  .cta-dlg h3{margin:18px 0 8px;font:700 13px "Segoe UI";text-transform:uppercase;letter-spacing:.06em;color:var(--accent2,#5ee1ff)}
  .cta-yo{display:flex;gap:14px;align-items:center}
  .cta-yo .cta-av{width:52px;height:52px;font-size:20px}
  .cta-mut{color:var(--muted,#8494b4)}
  .cta-disp{border:1px solid var(--line,#1a2740);border-radius:12px;padding:10px 12px;margin-bottom:8px;display:flex;gap:12px;align-items:flex-start;justify-content:space-between}
  .cta-disp b{font-size:14px}
  .cta-tag{display:inline-block;font:700 10px "Segoe UI";padding:1px 7px;border-radius:99px;margin-left:6px;vertical-align:middle}
  .cta-tag.yo{background:#3d8bff;color:#fff}.cta-tag.nuevo{background:#ffb547;color:#2a1800}.cta-tag.cerrada{background:#3a4660;color:#cfd8ea}.cta-tag.pedida{background:#ff6b7d;color:#2a0006}
  .cta-btn{padding:6px 12px;border-radius:9px;border:1px solid var(--line2,#24365a);background:var(--surface2,#101829);color:inherit;font:600 12px "Segoe UI";cursor:pointer;white-space:nowrap}
  .cta-btn.peligro{border-color:var(--err,#ff6b7d);color:var(--err,#ff6b7d)}
  .cta-fila{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .cta-tips{margin:0;padding-left:18px;color:var(--muted,#8494b4)}
  .cta-tips a{color:var(--accent2,#5ee1ff)}
  .cta-velo{overflow-y:auto;padding:24px 0}
  .cta-caja.ancha{width:min(680px,94vw);text-align:left}
  .cta-caja.ancha h1{font-size:21px}
  .cta-pasos{display:flex;gap:6px;flex-wrap:wrap;font:12px "Segoe UI",system-ui,sans-serif;margin-bottom:14px}
  .cta-pasos span{padding:3px 10px;border-radius:99px;border:1px solid var(--line2,#24365a);color:var(--muted,#8494b4)}
  .cta-pasos span.hecho{color:var(--ok,#34d399);border-color:var(--ok,#34d399)}
  .cta-pasos span.ahora{color:var(--ink,#edf3ff);border-color:var(--accent,#3d8bff);font-weight:700}
  .cta-op{border:1px solid var(--line2,#24365a);border-radius:14px;padding:12px 14px;margin-bottom:10px;display:grid;gap:8px;background:var(--surface,#0b111e)}
  .cta-op>b{font-size:14px}
  .cta-op input{margin:0}
  .cta-op .cta-dos{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .cta-op label{display:grid;gap:3px;font-size:12px;color:var(--muted,#8494b4)}
  .cta-etq{font:700 11px "Segoe UI";text-transform:uppercase;letter-spacing:.06em;color:var(--muted,#8494b4)}
  .cta-lista{display:grid;gap:5px;max-height:26vh;overflow-y:auto}
  .cta-lista button{text-align:left;padding:8px 11px;border-radius:9px;border:1px solid var(--line2,#24365a);background:var(--surface2,#101829);color:inherit;font:inherit;cursor:pointer}
  .cta-lista button:hover,.cta-lista button:focus-visible{border-color:var(--accent,#3d8bff);outline:none}
  .cta-linkbox{display:flex;gap:8px;align-items:center;padding:9px 12px;border-radius:10px;background:var(--surface2,#101829);word-break:break-all}
  .cta-btn.pri{background:var(--accent,#3d8bff);border-color:var(--accent,#3d8bff);color:#fff}
  .cta-caja ol{margin:0 0 14px;padding-left:20px;display:grid;gap:4px}
  .cta-caja details{margin:10px 0;color:var(--muted,#8494b4)}
  @media (max-width:560px){.cta-op .cta-dos{grid-template-columns:1fr}}
  .cta-gira{display:inline-block;animation:ctaGira 1s linear infinite}
  @keyframes ctaGira{to{transform:rotate(360deg)}}
  .cta-cortina{border:0;padding:0;background:transparent;max-width:none;max-height:none;width:100vw;height:100vh;margin:0;display:none;place-items:center;color:var(--ink,#edf3ff)}
  .cta-cortina[open]{display:grid}
  .cta-cortina::backdrop{background:rgba(4,6,11,.8);backdrop-filter:blur(3px)}
  .cta-cortina>div{display:grid;gap:10px;justify-items:center;text-align:center;padding:26px 30px;border-radius:16px;background:var(--surface,#0b111e);border:1px solid var(--line2,#24365a);box-shadow:0 20px 50px rgba(0,0,0,.35);max-width:min(440px,90vw);font:14px/1.5 "Segoe UI",system-ui,sans-serif}
  .cta-cortina b{font-size:20px}
  .cta-cortina .cta-gira{font-size:34px}
  @media (prefers-reduced-motion:reduce){.cta-gira{animation-duration:3s}}`;
  const G = `<svg width="18" height="18" viewBox="0 0 48 48"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>`;
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pedir = async (ruta, cuerpo) => {
    const r = await fetch(ruta, cuerpo === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) });
    const d = await r.json();
    if (d.ok === false || (!r.ok && d.error)) throw new Error(d.error || "Error");
    return d;
  };
  const avatar = (c, clase = "cta-av") => c.foto
    ? `<img class="${clase}" src="${esc(c.foto)}" alt="" referrerpolicy="no-referrer">`
    : `<span class="${clase}">${esc((c.nombre || c.email || "?").trim()[0] || "?").toUpperCase()}</span>`;
  const fecha = t => t ? t.replace(/^(\d{4})-(\d\d)-(\d\d) /, "$3/$2/$1 ") : "—";
  let opc = {}, estado = {}, velo = null, dlg = null;

  function estilo() {
    if (document.getElementById("cta-css")) return;
    const s = document.createElement("style"); s.id = "cta-css"; s.textContent = css; document.head.appendChild(s);
  }

  // Entrar y elegir la carpeta del proyecto, paso a paso: entrar con Google → carpeta (propia, compartida, nueva o
  // por link) → invitar al equipo. Lo mismo sirve para cambiar de carpeta después (Cuenta.elegirCarpeta).
  const PASOS = [["entrar", "Entrar"], ["carpeta", "Carpeta"], ["equipo", "Equipo"]];
  function pantallaEntrar(desde) {
    return new Promise(listo => {
      velo?.remove();
      velo = document.createElement("div");
      velo.className = "cta-velo";
      document.body.appendChild(velo);
      const W = { paso: desde || (estado.con_llave && estado.sin_carpeta ? "carpeta" : "entrar"), carpetas: null, res: null, cambiando: !!desde };
      const terminar = async eligio => {
        try { estado = await pedir("/api/cuenta"); } catch (e) {}
        velo?.remove(); velo = null;
        if (estado.conectado) pintarChip();
        if (eligio && W.res) await opc.alElegirCarpeta?.(W.res);
        listo(eligio ? estado : null);
      };
      const nota = (t, err) => { const n = velo.querySelector("#cta-nota"); if (n) { n.textContent = t; n.className = "cta-nota" + (err ? " err" : ""); } };
      const pasos = () => {
        const i = PASOS.findIndex(p => p[0] === (W.paso === "solo_lectura" ? "carpeta" : W.paso));
        return `<div class="cta-pasos">${PASOS.map(([k, t], j) => `<span class="${j < i ? "hecho" : j === i ? "ahora" : ""}">${j < i ? "✓ " : ""}${t}</span>`).join("")}</div>`;
      };
      const cabeza = `<div style="display:flex;gap:14px;align-items:center;margin-bottom:12px"><div class="cta-logo" style="background:${opc.fondo};width:46px;height:46px;font-size:22px;border-radius:13px;margin:0">${esc(opc.letra)}</div>
        <div><div class="cta-mut">${esc(opc.app)}${W.cambiando ? "" : " · primera vez en esta PC"}</div></div></div>`;
      const filas = (lista, vacio) => {
        const q = (velo.querySelector("#cta-buscar")?.value || "").trim().toLowerCase();
        const ver = (lista || []).filter(c => !q || c.nombre.toLowerCase().includes(q));
        return ver.map(c => `<button type="button" data-usar="${esc(c.id)}" data-nombre="${esc(c.nombre)}">📁 ${esc(c.nombre)}</button>`).join("")
          || `<p class="cta-mut" style="margin:0">${q ? "Ninguna carpeta con ese nombre." : vacio}</p>`;
      };
      const cargarCarpetas = async () => {
        W.carpetas = null; pintar();
        try { W.carpetas = await pedir("/api/cuenta/carpetas"); } catch (e) { W.carpetas = { mias: [], compartidas: [], error: e.message }; }
        pintar();
      };
      const usar = async cuerpo => {
        velo.querySelector(".cta-caja").innerHTML = cabeza + pasos() + `<p class="cta-mut">Conectando la carpeta y revisando tus permisos…</p>`;
        try {
          W.res = await pedir("/api/cuenta/primera", cuerpo);
          W.paso = W.res.editor ? "equipo" : "solo_lectura";
        } catch (e) { W.error = e.message; }
        pintar();
      };
      function pintar() {
        const caja = `<div class="cta-caja${W.paso === "entrar" && !W.cambiando ? "" : " ancha"}">`;
        if (W.paso === "entrar") {
          velo.innerHTML = `<div class="cta-caja">
            <div class="cta-logo" style="background:${opc.fondo}">${esc(opc.letra)}</div>
            <h1>${esc(opc.app)}</h1>
            <p>${estado.cerrada_desde_otra_pc ? "Tu sesión se cerró desde otro dispositivo. Vuelve a entrar." : "Entra con tu cuenta de Google. Después eliges la carpeta de Drive donde trabaja tu equipo (o creas una)."}</p>
            <button class="cta-google" id="cta-entrar">${G} Entrar con Google</button>
            <details style="text-align:left"><summary>Ya tengo el link de la carpeta del proyecto</summary>
              <input id="cta-link" style="margin-top:8px" placeholder="https://drive.google.com/drive/folders/…" value="${esc(estado.link || "")}">
              <button class="cta-btn" id="cta-con-link">Entrar con este link</button></details>
            <details style="text-align:left"><summary>Avanzado: credenciales propias de Google Cloud</summary>
              <p class="cta-mut" style="font-size:12px;margin:6px 0">Si tu equipo tiene su propio proyecto en Google Cloud (cliente OAuth «App de escritorio»), pega aquí su ID y secreto. Si no, déjalo vacío.</p>
              <input id="cta-cid" placeholder="ID de cliente (…apps.googleusercontent.com)"><input id="cta-secreto" type="password" autocomplete="off" placeholder="Secreto del cliente"></details>
            <div class="cta-nota" id="cta-nota"></div></div>`;
          const esperar = b => { velo.querySelectorAll("button").forEach(x => x.disabled = true); nota("Se abrió Google en tu navegador: elige tu cuenta y acepta. Esta ventana sigue sola."); };
          const soltar = e => { velo.querySelectorAll("button").forEach(x => x.disabled = false); nota(e.message, true); };
          velo.querySelector("#cta-entrar").onclick = async () => {
            esperar();
            try {
              const r = await pedir("/api/cuenta/primera", { accion: "entrar", client_id: velo.querySelector("#cta-cid").value, client_secret: velo.querySelector("#cta-secreto").value });
              estado = r.cuenta || estado; W.paso = "carpeta"; cargarCarpetas();
            } catch (e) { soltar(e); }
          };
          velo.querySelector("#cta-con-link").onclick = async () => {
            const link = velo.querySelector("#cta-link").value.trim();
            if (!link) return nota("Pega el link de la carpeta del proyecto (en Drive: Compartir → Copiar enlace).", true);
            esperar();
            try { estado = (await pedir("/api/entrar", { link })).cuenta; W.res = { link, nombre: estado.carpeta_nombre, editor: true }; terminar(true); } catch (e) { soltar(e); }
          };
          return;
        }
        if (W.paso === "carpeta") {
          if (!W.carpetas) { velo.innerHTML = caja + cabeza + pasos() + `<p class="cta-mut">Buscando tus carpetas en Drive…</p></div>`; return; }
          const k = W.carpetas, ant = k.anterior || {};
          velo.innerHTML = caja + cabeza + pasos() + `<h1>¿Dónde trabaja tu equipo?</h1>
            <p class="cta-mut">${estado.email ? `Entraste como <b>${esc(estado.email)}</b>. ` : ""}Elige una carpeta que ya tengas, una que te compartieron, o crea una nueva. Las tres apps trabajan en la misma carpeta.</p>
            ${W.error ? `<p class="cta-nota err">${esc(W.error)}</p>` : ""}${k.error ? `<p class="cta-nota err">No pude leer tus carpetas: ${esc(k.error)}</p>` : ""}
            ${ant.id ? `<div class="cta-op"><b>↩ La de antes</b><div class="cta-lista"><button type="button" data-usar="${esc(ant.id)}" data-nombre="${esc(ant.nombre || "")}">📁 ${esc(ant.nombre || "La carpeta que usabas")}</button></div></div>` : ""}
            <form class="cta-op" id="cta-crear"><b>✨ Crear mi carpeta</b>
              <div class="cta-dos"><label>Nombre de la carpeta<input id="cta-nombre" value="EDICIÓN DE VIDEOS" required></label>
                <label>Nombre de tu marca<input id="cta-marca" placeholder="Ej.: Suplevet"></label></div>
              <span class="cta-mut" style="font-size:12px">Se crea en tu «Mi unidad». Si ya existe una con ese nombre, se usa esa.</span>
              <div><button class="cta-btn pri">Crear y usar esta carpeta</button></div></form>
            <div class="cta-op"><b>📂 Usar una que ya existe</b>
              <input id="cta-buscar" type="search" placeholder="Buscar carpeta…" aria-label="Buscar carpeta">
              <span class="cta-etq">Compartidas conmigo</span><div class="cta-lista" id="cta-comp">${filas(k.compartidas, "Nadie te compartió carpetas todavía. Si tu equipo ya tiene una, pide que te la compartan como Editor.")}</div>
              <span class="cta-etq">Mi unidad</span><div class="cta-lista" id="cta-mias">${filas(k.mias, "No hay carpetas en tu «Mi unidad».")}</div></div>
            <form class="cta-op" id="cta-linkf"><b>🔗 Pegar el link de la carpeta</b>
              <div class="cta-fila"><input id="cta-link2" style="flex:1" placeholder="https://drive.google.com/drive/folders/…" required><button class="cta-btn">Usar este link</button></div></form>
            <div class="cta-fila" style="justify-content:space-between;margin-top:6px">
              <button type="button" class="cta-btn" id="cta-recargar">↻ Volver a buscar</button>
              ${W.cambiando ? `<button type="button" class="cta-btn" id="cta-cancelar">Cancelar</button>` : `<button type="button" class="cta-btn" id="cta-otra-cuenta">Entrar con otra cuenta</button>`}</div></div>`;
          W.error = "";
          velo.querySelector("#cta-buscar").oninput = () => {
            velo.querySelector("#cta-comp").innerHTML = filas(k.compartidas, "—"); velo.querySelector("#cta-mias").innerHTML = filas(k.mias, "—");
          };
          velo.querySelector("#cta-crear").onsubmit = e => { e.preventDefault(); usar({ accion: "crear", nombre: velo.querySelector("#cta-nombre").value.trim(), marca: velo.querySelector("#cta-marca").value.trim() }); };
          velo.querySelector("#cta-linkf").onsubmit = e => { e.preventDefault(); usar({ accion: "link", link: velo.querySelector("#cta-link2").value.trim() }); };
          velo.querySelector("#cta-recargar").onclick = cargarCarpetas;
          velo.querySelector("#cta-cancelar")?.addEventListener("click", () => terminar(false));
          velo.querySelector("#cta-otra-cuenta")?.addEventListener("click", () => { W.paso = "entrar"; pintar(); });
          velo.onclick = e => { const b = e.target.closest("[data-usar]"); if (b) usar({ accion: "usar", id: b.dataset.usar, nombre: b.dataset.nombre }); };
          return;
        }
        velo.onclick = null;
        if (W.paso === "solo_lectura") {
          velo.innerHTML = caja + cabeza + pasos() + `<h1>En «${esc(W.res.nombre || "esa carpeta")}» solo puedes mirar</h1>
            <p>Tu cuenta tiene permiso de <b>lector</b>: puedes abrir lo que hay, pero las apps no pueden subir nada.
              Pide a quien es dueño de la carpeta que te la comparta como <b>Editor</b> (en Drive: clic derecho → Compartir → tu correo → «Editor»).</p>
            <div class="cta-fila"><button class="cta-btn pri" id="cta-otra">Elegir otra carpeta</button><button class="cta-btn" id="cta-igual">Seguir solo mirando</button></div></div>`;
          velo.querySelector("#cta-otra").onclick = () => { W.paso = "carpeta"; cargarCarpetas(); };
          velo.querySelector("#cta-igual").onclick = () => { W.paso = "equipo"; pintar(); };
          return;
        }
        if (W.paso === "equipo") {
          const link = W.res?.link || "";
          velo.innerHTML = caja + cabeza + pasos() + `<h1>Invita a tu equipo</h1>
            <p class="cta-mut">Listo: trabajas en <b>${esc(W.res?.nombre || "la carpeta")}</b>. Todos trabajan en la misma carpeta: quien graba, quien diseña, quien edita y quien publica. Compártela como <b>Editor</b> (con «Lector» las apps no pueden subir nada).</p>
            <div class="cta-linkbox"><span style="flex:1">${esc(link)}</span><button class="cta-btn" id="cta-copiar">Copiar link</button></div>
            <ol style="margin-top:12px"><li>Toca «Abrir en Drive».</li><li>Arriba, toca <b>Compartir</b>.</li><li>Escribe el correo de cada persona, elige <b>Editor</b> y toca <b>Enviar</b>.</li>
              <li>Cada persona instala la app, entra con su cuenta y elige la carpeta en «Compartidas conmigo».</li></ol>
            <div class="cta-fila" style="justify-content:space-between"><a class="cta-btn" href="${esc(link)}" target="_blank" rel="noopener" style="text-decoration:none">Abrir en Drive</a>
              <button class="cta-btn pri" id="cta-listo">Empezar →</button></div></div>`;
          velo.querySelector("#cta-copiar").onclick = async e => {
            try { await navigator.clipboard.writeText(link); } catch (er) {
              const t = document.createElement("textarea"); t.value = link; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove();
            }
            e.target.textContent = "Copiado ✓";
          };
          velo.querySelector("#cta-listo").onclick = () => terminar(true);
        }
      }
      if (W.paso === "carpeta") cargarCarpetas(); else pintar();
    });
  }

  function pintarChip() {
    if (!opc.lugar) return;
    opc.lugar.innerHTML = `<button class="cta-chip" title="Tu cuenta, dispositivos y seguridad">${avatar(estado)}<span>${esc(estado.nombre || estado.email)}</span></button>`;
    opc.lugar.querySelector("button").onclick = abrirPanel;
  }

  let caja = null;  // donde está dibujado el panel ahora (la ventana o una página de la app)
  function montar(el, enVentana) {
    caja = el;
    el.innerHTML = `<div class="cta-yo">${avatar(estado)}<div><h2>${esc(estado.nombre || "Tu cuenta")}</h2>
        <div class="cta-mut">${esc(estado.email)}</div><div class="cta-mut">Esta PC: ${esc(estado.dispositivo)}</div></div>
        <span style="flex:1"></span><button class="cta-btn peligro" data-cta="salir">Cerrar sesión</button></div>
      <h3>Carpeta del proyecto</h3>
      <div class="cta-disp"><div><b>📁 ${esc(estado.carpeta_nombre || "Carpeta de Drive")}</b>
          <div class="cta-mut">Las tres apps trabajan aquí. Para sumar a alguien, compártela como Editor.</div></div>
        <div class="cta-fila"><a class="cta-btn" href="${esc(estado.link || "https://drive.google.com")}" target="_blank" rel="noopener" style="text-decoration:none">Abrir en Drive</a>
          <button class="cta-btn" data-cta="carpeta">Cambiar de carpeta…</button></div></div>
      <h3>Dispositivos con tu cuenta</h3><div id="cta-lista" class="cta-mut">Buscando en Drive…</div>
      <div class="cta-fila" style="margin-top:6px"><button class="cta-btn" data-cta="otros">Cerrar sesión en todos los demás</button></div>
      <h3>Seguridad</h3>
      <ul class="cta-tips">
        <li>Solo entra quien tenga compartida la carpeta del proyecto en Drive. Para quitarle el acceso a alguien de verdad, quítale la carpeta: <a href="${esc(estado.link || "https://drive.google.com")}" target="_blank" rel="noopener">abrir la carpeta en Drive</a> → Compartir.</li>
        <li>«Cerrar sesión ahí» cierra la sesión de esa PC en su siguiente revisión (máximo 10 minutos).</li>
        <li>Si ves un dispositivo que no reconoces, ciérralo y cambia la contraseña de tu Google: <a href="https://myaccount.google.com/device-activity" target="_blank" rel="noopener">actividad de tu cuenta de Google</a>.</li>
        <li>Las llaves nunca salen de cada PC: no viajan por Drive ni por GitHub.</li>
      </ul>
      ${enVentana ? '<div class="cta-fila" style="justify-content:flex-end;margin-top:16px"><button class="cta-btn" data-cta="cerrar">Listo</button></div>' : ""}`;
    el.onclick = async e => {
      const b = e.target.closest("[data-cta]"); if (!b) return;
      const a = b.dataset.cta;
      try {
        if (a === "cerrar") return dlg?.close();
        if (a === "carpeta") { dlg?.close(); if (await elegirCarpeta()) montar(el, enVentana); return; }
        if (a === "salir") {
          if (!confirm("¿Cerrar sesión en esta PC? Las tres apps volverán a pedir entrar con Google.")) return;
          await pedir("/api/salir", {}); dlg?.close(); estado = await pedir("/api/cuenta"); return volverAEntrar();
        }
        if (a === "otros") {
          if (!confirm("¿Cerrar la sesión en todos los demás dispositivos?")) return;
          const r = await pedir("/api/cerrar_otros", {}); b.textContent = `Pedido en ${r.cerrados} dispositivo(s)`; return cargarDispositivos();
        }
        if (a === "uno") { await pedir("/api/cerrar_dispositivo", { id: b.dataset.id }); b.textContent = "Cierre pedido"; b.disabled = true; }
      } catch (er) { alert(er.message); }
    };
    cargarDispositivos();
  }

  function abrirPanel() {
    if (opc.alAbrirCuenta) return opc.alAbrirCuenta();  // la app lo muestra en su propia página
    dlg?.remove();
    dlg = document.createElement("dialog");
    dlg.className = "cta-dlg";
    document.body.appendChild(dlg);
    montar(dlg, true);
    dlg.showModal();
  }

  async function cargarDispositivos() {
    const lista = caja?.querySelector("#cta-lista"); if (!lista) return;
    try {
      const { dispositivos, dias_nuevo } = await pedir("/api/dispositivos");
      lista.className = "";
      lista.innerHTML = dispositivos.length ? dispositivos.map(d => `<div class="cta-disp"><div>
          <b>${esc(d.equipo)}</b>${d.esta_pc ? '<span class="cta-tag yo">Esta PC</span>' : ""}${d.nuevo ? `<span class="cta-tag nuevo" title="Entró por primera vez en los últimos ${dias_nuevo} días">Nuevo</span>` : ""}${d.estado === "cerrada" ? '<span class="cta-tag cerrada">Sesión cerrada</span>' : ""}${d.cerrar && d.estado !== "cerrada" ? '<span class="cta-tag pedida">Cierre pedido</span>' : ""}
          <div class="cta-mut">${esc(d.sistema || "")}${d.usuario_windows ? " · usuario " + esc(d.usuario_windows) : ""}</div>
          <div class="cta-mut">${Object.entries(d.apps || {}).map(([n, v]) => `${esc(n)} ${esc(v.version)}`).join(" · ")}</div>
          <div class="cta-mut">Primera vez: ${fecha(d.primer_acceso)} · Última vez: ${fecha(d.ultimo_acceso)}</div></div>
          ${d.esta_pc || d.estado === "cerrada" ? "" : `<button class="cta-btn peligro" data-cta="uno" data-id="${esc(d.id)}" ${d.cerrar ? "disabled" : ""}>${d.cerrar ? "Cierre pedido" : "Cerrar sesión ahí"}</button>`}</div>`).join("")
        : `<p class="cta-mut">Aún no hay dispositivos anotados (se anotan al abrir cada app).</p>`;
    } catch (e) { lista.textContent = "No pude leer los dispositivos: " + e.message; }
  }

  // Cambiar de carpeta con la sesión abierta: devuelve la cuenta si eligió una, o null si canceló.
  async function elegirCarpeta() {
    estilo();
    return pantallaEntrar("carpeta");
  }

  async function volverAEntrar() {
    if (opc.lugar) opc.lugar.innerHTML = "";
    await pantallaEntrar();
    opc.alVolver?.();
  }

  async function vigilar() {  // si la sesión se cierra desde otra PC, la pantalla de entrar vuelve sola
    try {
      const c = await pedir("/api/cuenta");
      if (!c.conectado && !velo) { estado = c; dlg?.close(); volverAEntrar(); }
      else if (c.conectado) { const cambio = c.email !== estado.email || c.foto !== estado.foto; estado = c; if (cambio) pintarChip(); }
    } catch (e) {}
  }

  window.Cuenta = {
    async iniciar(o) {
      opc = o; estilo();
      estado = await pedir("/api/cuenta");
      if (!estado.conectado) await pantallaEntrar();
      pintarChip();
      setInterval(vigilar, 60000);
      return estado;
    },
    estado: () => estado,
    abrirPanel,
    elegirCarpeta,
    pintarEn: el => { estilo(); montar(el, false); },
  };

  // Instalar la versión nueva, igual en las tres apps: los botones dicen «⟳ Actualizando…», una cortina tapa la app
  // (Esc no la cierra) y cuando la versión nueva responde la página se recarga sola. Uso:
  //   Actualizar.instalar({ app: "la Sala de diseño", nueva: "1.2.0", botones: [b1, b2], version: "/api/version?forzar" })
  // Devuelve una promesa que falla (con la cortina ya quitada) si no se pudo instalar.
  function ponerActualizando(botones, texto = "Actualizando…") {
    const antes = [...new Set(botones)].filter(b => b instanceof HTMLElement).map(b => [b, b.innerHTML, b.disabled]);
    antes.forEach(([b]) => { b.innerHTML = `<span class="cta-gira" aria-hidden="true">⟳</span> ${esc(texto)}`; b.disabled = true; b.setAttribute("aria-busy", "true"); });
    return () => antes.forEach(([b, html, dis]) => { b.innerHTML = html; b.disabled = dis; b.removeAttribute("aria-busy"); });
  }
  function cortina(titulo, detalle) {
    estilo();
    let c = document.getElementById("cta-cortina");
    if (!c) {
      c = document.createElement("dialog"); c.id = "cta-cortina"; c.className = "cta-cortina"; c.setAttribute("aria-live", "assertive");
      c.addEventListener("cancel", e => e.preventDefault());
      document.body.appendChild(c);
    }
    c.innerHTML = `<div><span class="cta-gira" aria-hidden="true">⟳</span><b>${esc(titulo)}</b><span class="cta-mut" style="font-size:13px">${esc(detalle)}</span></div>`;
    if (!c.open) c.showModal();
  }
  const quitarCortina = () => { const c = document.getElementById("cta-cortina"); if (c?.open) c.close(); };
  window.Actualizar = {
    ponerActualizando,
    instalar({ app = "la app", nueva = "", botones = [], version = "/api/version?forzar" } = {}) {
      estilo();
      const volver = ponerActualizando(botones);
      cortina(`Actualizando${nueva ? ` a la v${nueva}` : ""}…`, `Bajando la versión nueva. No cierres ${app}: se reinicia en unos segundos.`);
      return new Promise(async (ok, mal) => {
        const fallo = msg => { quitarCortina(); volver(); mal(new Error(msg)); };
        let r;
        try { r = await pedir("/api/actualizar", {}); } catch (e) { return fallo(e.message); }
        cortina(`Actualizando a la v${r.version}…`, "Versión nueva instalada. Reiniciando…");
        const desde = Date.now();
        const esperar = async () => {
          try { const v = await (await fetch(version)).json(); if (v.actual === r.version) { ok(r.version); return location.reload(); } } catch (e) {}
          if (Date.now() - desde > 90000) return fallo(`La versión nueva no arrancó en 90 segundos. Cierra ${app} y vuelve a abrir; si no abre, baja el instalador completo.`);
          setTimeout(esperar, 1500);
        };
        setTimeout(esperar, 2500);
      });
    },
  };
})();
