"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CalendarDays, Folder, FolderPlus, Inbox, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { createClient } from "@/lib/supabase/client";
import { TarjetaVideo } from "@/components/TarjetaVideo";
import { VideoDetalle } from "@/components/VideoDetalle";
import { COLORES_CARPETA, ordenarSesiones, type Carpeta, type ColorCarpeta, type Video } from "@/lib/videos";

// Dos formas de ordenar: por sesión de grabación (automático) y por carpetas propias (campañas, temas, anuncios).
// Para mover un video: arrástralo a una carpeta, o ábrelo y elige la carpeta en su ficha.
type Seleccion = { tipo: "sesion"; id: string } | { tipo: "carpeta"; id: string } | { tipo: "sin" };

export default function CarpetasPage() {
  const [videos, setVideos] = useState<Video[]>([]);
  const [carpetas, setCarpetas] = useState<Carpeta[]>([]);
  const [sel, setSel] = useState<Seleccion>({ tipo: "sin" });
  const [encima, setEncima] = useState<string | null>(null);
  const [abierto, setAbierto] = useState<Video | null>(null);
  const [nueva, setNueva] = useState("");
  const [color, setColor] = useState<ColorCarpeta>("navy");

  const cargar = useCallback(async () => {
    const supabase = createClient();
    const [{ data: v }, { data: c }] = await Promise.all([
      supabase.from("videos").select("*").order("semana", { ascending: false }).order("codigo"),
      supabase.from("carpetas").select("*").order("orden").order("nombre"),
    ]);
    setVideos((v as Video[]) ?? []);
    setCarpetas((c as Carpeta[]) ?? []);
  }, []);

  useEffect(() => {
    cargar();
    window.addEventListener("sesion-renovada", cargar);
    return () => window.removeEventListener("sesion-renovada", cargar);
  }, [cargar]);

  const sesiones = useMemo(() => {
    const m = new Map<string, Video[]>();
    videos.forEach((v) => m.set(v.semana, [...(m.get(v.semana) ?? []), v]));
    return ordenarSesiones([...m.keys()]).map((s) => [s, m.get(s)!] as const);
  }, [videos]);

  const lista = videos.filter((v) =>
    sel.tipo === "sesion" ? v.semana === sel.id : sel.tipo === "carpeta" ? v.carpeta_id === sel.id : !v.carpeta_id
  );
  const titulo =
    sel.tipo === "sesion" ? sel.id : sel.tipo === "carpeta" ? carpetas.find((c) => c.id === sel.id)?.nombre ?? "" : "Sin carpeta";

  async function mover(videoId: string, carpetaId: string | null) {
    const v = videos.find((x) => x.id === videoId);
    if (!v || v.carpeta_id === carpetaId) return;
    setVideos((vs) => vs.map((x) => (x.id === videoId ? { ...x, carpeta_id: carpetaId } : x)));
    const { error } = await createClient().from("videos").update({ carpeta_id: carpetaId }).eq("id", videoId);
    if (error) {
      toast.error("No se pudo mover: " + error.message);
      cargar();
    } else toast.success(`${v.codigo} → ${carpetaId ? carpetas.find((c) => c.id === carpetaId)?.nombre : "Sin carpeta"}`);
  }

  async function crear(e: React.FormEvent) {
    e.preventDefault();
    if (!nueva.trim()) return toast.error("Escribe un nombre para la carpeta.");
    const { error } = await createClient().from("carpetas").insert({ nombre: nueva.trim(), color, orden: carpetas.length });
    if (error) return toast.error(error.message);
    setNueva("");
    cargar();
  }

  async function renombrar(c: Carpeta) {
    const nombre = prompt("Nuevo nombre de la carpeta:", c.nombre)?.trim();
    if (!nombre || nombre === c.nombre) return;
    const { error } = await createClient().from("carpetas").update({ nombre }).eq("id", c.id);
    if (error) toast.error(error.message);
    cargar();
  }

  async function borrar(c: Carpeta) {
    if (!confirm(`¿Borrar la carpeta «${c.nombre}»? Sus videos no se borran: quedan sin carpeta.`)) return;
    const { error } = await createClient().from("carpetas").delete().eq("id", c.id);
    if (error) return toast.error(error.message);
    if (sel.tipo === "carpeta" && sel.id === c.id) setSel({ tipo: "sin" });
    cargar();
  }

  const zona = (clave: string, carpetaId: string | null) => ({
    onDragOver: (e: React.DragEvent) => {
      e.preventDefault();
      setEncima(clave);
    },
    onDragLeave: () => setEncima((x) => (x === clave ? null : x)),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      setEncima(null);
      const id = e.dataTransfer.getData("text/video-id");
      if (id) mover(id, carpetaId);
    },
  });

  const mosaico = (vs: Video[]) => (
    <span className="flex -space-x-3" aria-hidden="true">
      {vs.filter((v) => v.portada_url).slice(0, 3).map((v) => (
        <span key={v.id} className="h-10 w-7 rounded-md border-2 border-white bg-navy bg-cover bg-center shadow-tarjeta"
          style={{ backgroundImage: `url('${v.portada_url}')` }} />
      ))}
    </span>
  );

  const item = (activo: boolean, sobre: boolean) =>
    `group flex w-full min-h-14 cursor-pointer items-center gap-3 rounded-xl px-3 py-2 text-left transition-all duration-200 ${
      sobre ? "scale-[1.02] bg-sky ring-2 ring-navy" : activo ? "bg-navy text-white shadow-alzada" : "hover:bg-sky-suave"}`;

  return (
    <div className="flex flex-col gap-6">
      <section className="tarjeta relative z-10 p-5 sm:p-6">
        <p className="etiqueta">Ordenar</p>
        <h1 className="text-4xl leading-none sm:text-5xl">Carpetas</h1>
        <p className="mt-1 max-w-2xl text-sm text-navy/70">
          Cada video vive en su sesión de grabación y, si quieres, también en una carpeta tuya (una campaña, un tipo de paciente, los
          anuncios de Meta). <b>Arrastra una portada a la carpeta</b>, o ábrela y elige la carpeta en su ficha.
        </p>
      </section>

      <div className="grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="flex flex-col gap-4">
          <div className="tarjeta p-3">
            <h2 className="px-2 pb-2 pt-1 text-xl">Mis carpetas</h2>
            <ul className="flex flex-col gap-1">
              <li>
                <button type="button" className={item(sel.tipo === "sin", encima === "sin")} onClick={() => setSel({ tipo: "sin" })} {...zona("sin", null)}>
                  <Inbox className="h-5 w-5 shrink-0" aria-hidden="true" />
                  <span className="flex-1 font-medium">Sin carpeta</span>
                  <span className="text-xs opacity-70">{videos.filter((v) => !v.carpeta_id).length}</span>
                </button>
              </li>
              {carpetas.map((c) => {
                const vs = videos.filter((v) => v.carpeta_id === c.id);
                const activo = sel.tipo === "carpeta" && sel.id === c.id;
                return (
                  <li key={c.id} className="relative">
                    <button type="button" className={item(activo, encima === c.id)} onClick={() => setSel({ tipo: "carpeta", id: c.id })} {...zona(c.id, c.id)}>
                      <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${COLORES_CARPETA[c.color].fondo} ${COLORES_CARPETA[c.color].texto}`}>
                        <Folder className="h-5 w-5" aria-hidden="true" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{c.nombre}</span>
                        <span className="text-xs opacity-70">{vs.length} videos</span>
                      </span>
                      {mosaico(vs)}
                    </button>
                    <span className="absolute right-1 top-1 flex opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 [li:hover_&]:opacity-100">
                      <button type="button" aria-label={`Renombrar ${c.nombre}`} onClick={() => renombrar(c)}
                        className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/90 text-navy shadow-tarjeta hover:bg-sky-suave">
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button type="button" aria-label={`Borrar ${c.nombre}`} onClick={() => borrar(c)}
                        className="ml-1 flex h-8 w-8 items-center justify-center rounded-lg bg-white/90 text-naranja-tinta shadow-tarjeta hover:bg-naranja/15">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
            <form onSubmit={crear} className="mt-3 grid gap-2 border-t border-navy/10 px-1 pt-3">
              <label className="text-xs font-medium text-navy/70" htmlFor="nueva-carpeta">Nueva carpeta</label>
              <input id="nueva-carpeta" className="campo" placeholder="Ej.: Posquirúrgico, Anuncios Meta" value={nueva} onChange={(e) => setNueva(e.target.value)} />
              <div className="flex items-center gap-2" role="radiogroup" aria-label="Color de la carpeta">
                {(Object.keys(COLORES_CARPETA) as ColorCarpeta[]).map((k) => (
                  <button key={k} type="button" role="radio" aria-checked={color === k} aria-label={COLORES_CARPETA[k].nombre} onClick={() => setColor(k)}
                    className={`h-9 w-9 cursor-pointer rounded-full border-2 transition-transform ${COLORES_CARPETA[k].fondo} ${color === k ? "scale-110 border-naranja" : "border-white shadow-tarjeta"}`} />
                ))}
                <button className="btn ml-auto">
                  <FolderPlus className="h-4 w-4" aria-hidden="true" /> Crear
                </button>
              </div>
            </form>
          </div>

          <div className="tarjeta p-3">
            <h2 className="px-2 pb-2 pt-1 text-xl">Sesiones de grabación</h2>
            <ul className="flex flex-col gap-1">
              {sesiones.map(([s, vs]) => (
                <li key={s}>
                  <button type="button" className={item(sel.tipo === "sesion" && sel.id === s, false)} onClick={() => setSel({ tipo: "sesion", id: s })}>
                    <CalendarDays className="h-5 w-5 shrink-0" aria-hidden="true" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{s}</span>
                      <span className="text-xs opacity-70">{vs.length} videos</span>
                    </span>
                    {mosaico(vs)}
                  </button>
                </li>
              ))}
              {sesiones.length === 0 && <li className="px-2 py-3 text-sm text-navy/60">Aún no hay videos subidos.</li>}
            </ul>
          </div>
        </aside>

        <section aria-live="polite">
          <div className="mb-3 flex items-baseline justify-between gap-2">
            <h2 className="text-3xl">{titulo}</h2>
            <span className="text-sm text-navy/60">{lista.length} videos</span>
          </div>
          {lista.length === 0 ? (
            <div className="tarjeta flex flex-col items-center gap-2 p-10 text-center text-sm text-navy/60">
              <Folder className="h-8 w-8 text-sky" aria-hidden="true" />
              Arrastra aquí portadas desde otra carpeta o sesión.
            </div>
          ) : (
            <div key={JSON.stringify(sel)} className="cascada grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-5">
              {lista.map((v, i) => (
                <TarjetaVideo key={v.id} video={v} indice={i} onAbrir={setAbierto} arrastrable />
              ))}
            </div>
          )}
        </section>
      </div>

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
