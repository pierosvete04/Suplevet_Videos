/* La cuenta en pantalla: entrar con Google, tu cuenta arriba y «Dispositivos y seguridad».
   Es el mismo archivo en las tres apps (junto con cuenta.py). Uso:
     await Cuenta.iniciar({ app: "Sala de diseño", letra: "D", fondo: "linear-gradient(...)", lugar: elementoDeLaCabeza });
   La promesa se cumple cuando hay sesión; si se cierra (aquí o desde otra PC) vuelve la pantalla de entrar. */
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
  .cta-tips a{color:var(--accent2,#5ee1ff)}`;
  const G = `<svg width="18" height="18" viewBox="0 0 48 48"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>`;
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pedir = async (ruta, cuerpo) => {
    const r = await fetch(ruta, cuerpo === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo) });
    const d = await r.json();
    if (d.ok === false) throw new Error(d.error || "Error");
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

  function pantallaEntrar() {
    return new Promise(listo => {
      velo?.remove();
      velo = document.createElement("div");
      velo.className = "cta-velo";
      velo.innerHTML = `<div class="cta-caja">
        <div class="cta-logo" style="background:${opc.fondo}">${esc(opc.letra)}</div>
        <h1>${esc(opc.app)}</h1>
        <p>${estado.cerrada_desde_otra_pc ? "Tu sesión se cerró desde otro dispositivo. Vuelve a entrar." : "Entra con tu cuenta de Google. Tienes que tener compartida la carpeta del proyecto en Drive."}</p>
        <input id="cta-link" placeholder="Link de la carpeta del proyecto en Drive" value="${esc(estado.link || "")}" ${estado.link ? "hidden" : ""}>
        ${estado.link ? `<p class="cta-mut" style="margin:-6px 0 12px">Proyecto guardado en esta PC · <a href="#" id="cta-otro" style="color:inherit">usar otro</a></p>` : ""}
        <button class="cta-google" id="cta-entrar">${G} Entrar con Google</button>
        <div class="cta-nota" id="cta-nota"></div></div>`;
      document.body.appendChild(velo);
      const nota = (t, err) => { const n = velo.querySelector("#cta-nota"); n.textContent = t; n.className = "cta-nota" + (err ? " err" : ""); };
      velo.querySelector("#cta-otro")?.addEventListener("click", e => { e.preventDefault(); const i = velo.querySelector("#cta-link"); i.hidden = false; i.value = ""; i.focus(); });
      velo.querySelector("#cta-entrar").onclick = async () => {
        const link = velo.querySelector("#cta-link").value.trim();
        if (!link) { nota("Pega el link de la carpeta del proyecto (en Drive: Compartir → Copiar enlace).", true); return; }
        const b = velo.querySelector("#cta-entrar"); b.disabled = true;
        nota("Se abrió Google en tu navegador: elige tu cuenta y acepta. Esta ventana sigue sola.");
        try {
          const r = await pedir("/api/entrar", { link });
          estado = r.cuenta; velo.remove(); velo = null; pintarChip(); listo(estado);
        } catch (e) { nota(e.message, true); b.disabled = false; }
      };
    });
  }

  function pintarChip() {
    if (!opc.lugar) return;
    opc.lugar.innerHTML = `<button class="cta-chip" title="Tu cuenta, dispositivos y seguridad">${avatar(estado)}<span>${esc(estado.nombre || estado.email)}</span></button>`;
    opc.lugar.querySelector("button").onclick = abrirPanel;
  }

  async function abrirPanel() {
    dlg?.remove();
    dlg = document.createElement("dialog");
    dlg.className = "cta-dlg";
    document.body.appendChild(dlg);
    dlg.innerHTML = `<div class="cta-yo">${avatar(estado)}<div><h2>${esc(estado.nombre || "Tu cuenta")}</h2>
        <div class="cta-mut">${esc(estado.email)}</div><div class="cta-mut">Esta PC: ${esc(estado.dispositivo)}</div></div>
        <span style="flex:1"></span><button class="cta-btn peligro" data-cta="salir">Cerrar sesión</button></div>
      <h3>Dispositivos con tu cuenta</h3><div id="cta-lista" class="cta-mut">Buscando en Drive…</div>
      <div class="cta-fila" style="margin-top:6px"><button class="cta-btn" data-cta="otros">Cerrar sesión en todos los demás</button></div>
      <h3>Seguridad</h3>
      <ul class="cta-tips">
        <li>Solo entra quien tenga compartida la carpeta del proyecto en Drive. Para quitarle el acceso a alguien de verdad, quítale la carpeta: <a href="${esc(estado.link || "https://drive.google.com")}" target="_blank" rel="noopener">abrir la carpeta en Drive</a> → Compartir.</li>
        <li>«Cerrar sesión ahí» cierra la sesión de esa PC en su siguiente revisión (máximo 10 minutos).</li>
        <li>Si ves un dispositivo que no reconoces, ciérralo y cambia la contraseña de tu Google: <a href="https://myaccount.google.com/device-activity" target="_blank" rel="noopener">actividad de tu cuenta de Google</a>.</li>
        <li>Las llaves nunca salen de cada PC: no viajan por Drive ni por GitHub.</li>
      </ul>
      <div class="cta-fila" style="justify-content:flex-end;margin-top:16px"><button class="cta-btn" data-cta="cerrar">Listo</button></div>`;
    dlg.onclick = async e => {
      const b = e.target.closest("[data-cta]"); if (!b) return;
      const a = b.dataset.cta;
      try {
        if (a === "cerrar") return dlg.close();
        if (a === "salir") {
          if (!confirm("¿Cerrar sesión en esta PC? Las tres apps volverán a pedir entrar con Google.")) return;
          await pedir("/api/salir", {}); dlg.close(); estado = await pedir("/api/cuenta"); return volverAEntrar();
        }
        if (a === "otros") {
          if (!confirm("¿Cerrar la sesión en todos los demás dispositivos?")) return;
          const r = await pedir("/api/cerrar_otros", {}); b.textContent = `Pedido en ${r.cerrados} dispositivo(s)`; return cargarDispositivos();
        }
        if (a === "uno") { await pedir("/api/cerrar_dispositivo", { id: b.dataset.id }); b.textContent = "Cierre pedido"; b.disabled = true; }
      } catch (er) { alert(er.message); }
    };
    dlg.showModal();
    cargarDispositivos();
  }

  async function cargarDispositivos() {
    const lista = dlg?.querySelector("#cta-lista"); if (!lista) return;
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
  };
})();
