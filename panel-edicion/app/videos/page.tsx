"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Film, LogOut } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { VideoDetalle } from "@/components/VideoDetalle";
import { ESTADOS, posicionHook, ranking, type EstadoVideo, type Video } from "@/lib/videos";

type Agrupar = "hook" | "tipo" | "cuerpo" | "cta";
const AGRUPAR: { valor: Agrupar; etiqueta: string; fn: (v: Video) => string }[] = [
  { valor: "hook", etiqueta: "Posición de hook", fn: posicionHook },
  { valor: "tipo", etiqueta: "Tipo de hook", fn: (v) => v.tipo_hook ?? "—" },
  { valor: "cuerpo", etiqueta: "Cuerpo", fn: (v) => v.cuerpo ?? "—" },
  { valor: "cta", etiqueta: "CTA", fn: (v) => v.cta ?? "—" },
];
const pct = (n: number | null) => (n == null ? "—" : `${n.toFixed(1)}%`);
const soles = (n: number | null) => (n == null ? "—" : `S/ ${n.toFixed(2)}`);

export default function VideosPage() {
  const [videos, setVideos] = useState<Video[]>([]);
  const [miembro, setMiembro] = useState<{ nombre: string } | null | undefined>(undefined);
  const [cargando, setCargando] = useState(true);
  const [semana, setSemana] = useState("");
  const [estado, setEstado] = useState<EstadoVideo | "todos">("todos");
  const [buscar, setBuscar] = useState("");
  const [agrupar, setAgrupar] = useState<Agrupar>("hook");
  const [abierto, setAbierto] = useState<Video | null>(null);

  const cargar = useCallback(async () => {
    setCargando(true);
    const supabase = createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    const [{ data: yo }, { data }] = await Promise.all([
      supabase.from("miembros").select("nombre").eq("id", user?.id ?? "").eq("activo", true).maybeSingle(),
      supabase.from("videos").select("*").order("codigo"),
    ]);
    setMiembro(yo ?? null);
    const lista = (data as Video[]) ?? [];
    setVideos(lista);
    setSemana((s) => s || [...new Set(lista.map((v) => v.semana))].sort().at(-1) || "");
    setCargando(false);
  }, []);

  useEffect(() => {
    cargar();
  }, [cargar]);

  const semanas = useMemo(() => [...new Set(videos.map((v) => v.semana))].sort().reverse(), [videos]);
  const deLaSemana = useMemo(() => videos.filter((v) => !semana || v.semana === semana), [videos, semana]);
  const visibles = deLaSemana.filter(
    (v) => (estado === "todos" || v.estado === estado) && (!buscar || v.codigo.toLowerCase().includes(buscar.toLowerCase()))
  );
  const filas = useMemo(
    () => ranking(deLaSemana.filter((v) => v.estado === "publicado"), AGRUPAR.find((a) => a.valor === agrupar)!.fn),
    [deLaSemana, agrupar]
  );

  if (miembro === null) {
    return (
      <main className="flex min-h-dvh items-center justify-center p-4 text-center">
        <div className="max-w-sm rounded-2xl border bg-white p-6">
          <p className="mb-4 text-sm">Tu cuenta no tiene acceso al panel de edición. Pídele al administrador que te agregue.</p>
          <form action="/salir" method="post">
            <button className="btn-sec">Cerrar sesión</button>
          </form>
        </div>
      </main>
    );
  }

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-20 border-b bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-widest text-naranja-tinta">Suplevet · Edición</p>
            <h1 className="text-lg font-bold text-navy">Videos</h1>
          </div>
          <div className="flex items-center gap-2">
            <label htmlFor="semana" className="text-sm text-slate-600">
              Semana
            </label>
            <select id="semana" className="campo w-auto" value={semana} onChange={(e) => setSemana(e.target.value)}>
              {semanas.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
            <form action="/salir" method="post">
              <button className="btn-sec" aria-label="Cerrar sesión" title={miembro?.nombre ? `Salir (${miembro.nombre})` : "Salir"}>
                <LogOut className="h-4 w-4" />
              </button>
            </form>
          </div>
        </div>
      </header>

      <main className="mx-auto flex max-w-7xl flex-col gap-5 px-4 py-5">
        <div className="flex flex-wrap items-center gap-2">
          {[{ valor: "todos" as const, etiqueta: "Todos" }, ...ESTADOS].map((e) => (
            <button
              key={e.valor}
              type="button"
              aria-pressed={estado === e.valor}
              onClick={() => setEstado(e.valor)}
              className={`rounded-full border px-3 py-1 text-sm ${estado === e.valor ? "border-navy bg-navy text-white" : "bg-white"}`}
            >
              {e.etiqueta} ({e.valor === "todos" ? deLaSemana.length : deLaSemana.filter((v) => v.estado === e.valor).length})
            </button>
          ))}
          <input
            type="search"
            className="campo ml-auto sm:w-56"
            placeholder="Buscar C1, H2, CTA-A…"
            aria-label="Buscar videos"
            value={buscar}
            onChange={(e) => setBuscar(e.target.value)}
          />
        </div>

        {cargando ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="aspect-[9/16] animate-pulse rounded-xl bg-slate-200" />
            ))}
          </div>
        ) : visibles.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-xl border bg-white p-10 text-center text-slate-500">
            <Film className="h-8 w-8" />
            <p>Todavía no hay videos aquí. Se suben desde la sala de edición con «Subir al panel».</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
            {visibles.map((v) => {
              const info = ESTADOS.find((e) => e.valor === v.estado)!;
              return (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => setAbierto(v)}
                  className="overflow-hidden rounded-xl border bg-white text-left transition-shadow hover:shadow-md"
                >
                  <div
                    className="relative aspect-[9/16] bg-black bg-cover bg-center"
                    style={v.portada_url ? { backgroundImage: `url('${v.portada_url}')` } : undefined}
                  >
                    <span className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 to-transparent px-2 pb-1.5 pt-6 font-mono text-[11px] font-bold text-white">
                      {v.codigo}
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-1 p-2 text-xs">
                    <span className={`rounded-full px-2 py-0.5 font-bold ${info.clase}`}>{info.etiqueta}</span>
                    {v.dia && <span className="text-slate-500">{v.dia.replace("_", " ")}</span>}
                    {typeof v.metricas.vistas === "number" && <span className="text-slate-500">· {v.metricas.vistas} vistas</span>}
                  </div>
                </button>
              );
            })}
          </div>
        )}

        <section className="rounded-xl border bg-white p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="font-semibold">Qué está ganando</h2>
              <p className="text-xs text-slate-500">Solo videos publicados. Con menos de ~300 vistas por video la diferencia puede ser azar.</p>
            </div>
            <select aria-label="Agrupar por" className="campo w-auto" value={agrupar} onChange={(e) => setAgrupar(e.target.value as Agrupar)}>
              {AGRUPAR.map((a) => (
                <option key={a.valor} value={a.valor}>
                  Por {a.etiqueta.toLowerCase()}
                </option>
              ))}
            </select>
          </div>
          {filas.length === 0 ? (
            <p className="text-sm text-slate-500">Cuando marques videos como publicados y cargues sus métricas, aquí aparece la comparación.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-sm">
                <thead className="text-left text-xs text-slate-500">
                  <tr>
                    {[AGRUPAR.find((a) => a.valor === agrupar)!.etiqueta, "Videos", "Vistas", "Retención 3 s", "Contactos", "Resultados ads", "Costo por resultado"].map((h) => (
                      <th key={h} className="py-2 pr-3 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filas.map((f) => (
                    <tr key={f.grupo} className="border-t">
                      <td className="py-2 pr-3 font-mono font-semibold">{f.grupo}</td>
                      <td className="py-2 pr-3">{f.videos}</td>
                      <td className="py-2 pr-3">{f.vistas.toLocaleString("es-PE")}</td>
                      <td className="py-2 pr-3">{pct(f.retencion3s)}</td>
                      <td className="py-2 pr-3">{f.contactos}</td>
                      <td className="py-2 pr-3">{f.resultadosAds}</td>
                      <td className="py-2">{soles(f.costoPorResultado)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>

      {abierto && (
        <VideoDetalle
          video={abierto}
          onClose={() => setAbierto(null)}
          onSaved={async () => {
            setAbierto(null);
            await cargar();
          }}
        />
      )}
    </div>
  );
}
