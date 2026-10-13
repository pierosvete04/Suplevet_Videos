"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, Clapperboard, Eye, Film, Search, Send } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { TarjetaVideo } from "@/components/TarjetaVideo";
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
  const [cargando, setCargando] = useState(true);
  const [semana, setSemana] = useState("todas");
  const [estado, setEstado] = useState<EstadoVideo | "todos">("todos");
  const [buscar, setBuscar] = useState("");
  const [agrupar, setAgrupar] = useState<Agrupar>("hook");
  const [abierto, setAbierto] = useState<Video | null>(null);

  const cargar = useCallback(async () => {
    setCargando(true);
    const { data } = await createClient().from("videos").select("*").order("semana", { ascending: false }).order("codigo");
    setVideos((data as Video[]) ?? []);
    setCargando(false);
  }, []);

  useEffect(() => {
    cargar();
    window.addEventListener("sesion-renovada", cargar);
    return () => window.removeEventListener("sesion-renovada", cargar);
  }, [cargar]);

  const semanas = useMemo(() => [...new Set(videos.map((v) => v.semana))], [videos]);
  const deLaSemana = useMemo(() => videos.filter((v) => semana === "todas" || v.semana === semana), [videos, semana]);
  const visibles = deLaSemana.filter(
    (v) => (estado === "todos" || v.estado === estado) && (!buscar || v.codigo.toLowerCase().includes(buscar.toLowerCase()))
  );
  const filas = useMemo(
    () => ranking(deLaSemana.filter((v) => v.estado === "publicado"), AGRUPAR.find((a) => a.valor === agrupar)!.fn),
    [deLaSemana, agrupar]
  );
  const cuenta = (e: EstadoVideo) => deLaSemana.filter((v) => v.estado === e).length;
  const vistas = deLaSemana.reduce((s, v) => s + (v.metricas?.vistas ?? 0), 0);

  const resumen = [
    { etiqueta: "Videos", valor: deLaSemana.length, icono: Film },
    { etiqueta: "Por revisar", valor: cuenta("borrador"), icono: Clapperboard },
    { etiqueta: "Aprobados", valor: cuenta("aprobado"), icono: CheckCircle2 },
    { etiqueta: "Publicados", valor: cuenta("publicado"), icono: Send },
    { etiqueta: "Vistas", valor: vistas.toLocaleString("es-PE"), icono: Eye },
  ];

  return (
    <div className="flex flex-col gap-6">
      <section className="tarjeta relative z-10 flex flex-wrap items-end justify-between gap-4 p-5 sm:p-6">
        <div>
          <p className="etiqueta">Sala de edición</p>
          <h1 className="text-4xl leading-none text-navy sm:text-5xl">Videos</h1>
          <p className="mt-1 max-w-xl text-sm text-navy/70">
            Revisa la portada y el copy de cada video, apruébalo y anota cómo le fue. Lo que arma la sala aparece aquí solo.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-navy/70">
          Sesión
          <select className="campo w-auto min-w-[11rem]" value={semana} onChange={(e) => setSemana(e.target.value)}>
            <option value="todas">Todas</option>
            {semanas.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
      </section>

      <section aria-label="Resumen" className="cascada grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {resumen.map(({ etiqueta, valor, icono: Icono }, i) => (
          <div key={etiqueta} style={{ ["--i" as string]: i }} className="tarjeta flex items-center gap-3 p-4">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-sky-suave text-navy">
              <Icono className="h-5 w-5" aria-hidden="true" />
            </span>
            <span>
              <span className="block font-impacto text-3xl leading-none tracking-wide">{cargando ? "·" : valor}</span>
              <span className="text-xs text-navy/60">{etiqueta}</span>
            </span>
          </div>
        ))}
      </section>

      <section className="flex flex-wrap items-center gap-2">
        {[{ valor: "todos" as const, etiqueta: "Todos" }, ...ESTADOS].map((e) => (
          <button key={e.valor} type="button" className="chip" aria-pressed={estado === e.valor} onClick={() => setEstado(e.valor)}>
            {e.etiqueta}
            <span className="text-xs opacity-70">{e.valor === "todos" ? deLaSemana.length : cuenta(e.valor)}</span>
          </button>
        ))}
        <label className="relative ml-auto w-full sm:w-64">
          <span className="sr-only">Buscar videos</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-navy/40" aria-hidden="true" />
          <input type="search" className="campo pl-9" placeholder="C1, H2, CTA-A…" value={buscar} onChange={(e) => setBuscar(e.target.value)} />
        </label>
      </section>

      {cargando ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="aspect-[9/16] animate-barrido rounded-2xl bg-[linear-gradient(90deg,#fff_0%,#E6F4F6_50%,#fff_100%)] bg-[length:200%_100%]" />
          ))}
        </div>
      ) : visibles.length === 0 ? (
        <div className="tarjeta flex flex-col items-center gap-3 p-12 text-center">
          <span className="flex h-16 w-16 items-center justify-center rounded-2xl bg-sky-suave">
            <Film className="h-8 w-8 text-navy" aria-hidden="true" />
          </span>
          <h2 className="text-2xl">Todavía no hay videos aquí</h2>
          <p className="max-w-md text-sm text-navy/70">
            En la sala de edición usa «Subir al panel» para los videos nuevos, o «Subir finales existentes» para traer los que ya están en
            V.FINALES de Drive.
          </p>
        </div>
      ) : (
        <div className="cascada grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
          {visibles.map((v, i) => (
            <TarjetaVideo key={v.id} video={v} indice={i} onAbrir={setAbierto} />
          ))}
        </div>
      )}

      <section className="tarjeta p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-2xl">Qué está ganando</h2>
            <p className="text-xs text-navy/60">Solo videos publicados. Con menos de ~300 vistas por video la diferencia puede ser azar.</p>
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
          <p className="text-sm text-navy/60">Cuando marques videos como publicados y cargues sus métricas, aquí aparece la comparación.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-sm">
              <thead className="text-left text-xs text-navy/60">
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
                  <tr key={f.grupo} className="border-t border-navy/10">
                    <td className="py-2 pr-3 font-impacto text-base tracking-wider">{f.grupo}</td>
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
