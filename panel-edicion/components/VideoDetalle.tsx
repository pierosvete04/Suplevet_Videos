"use client";

import { useEffect, useRef, useState } from "react";
import { Copy, Download, X } from "lucide-react";
import { toast } from "sonner";
import { createClient } from "@/lib/supabase/client";
import { VistaTelefono } from "@/components/VistaTelefono";
import { ESTADOS, REDES, type Carpeta, type CopyVideo, type EstadoVideo, type MetricasVideo, type RedSocial, type Video } from "@/lib/videos";

const CAMPOS_METRICAS: { clave: keyof MetricasVideo; etiqueta: string }[] = [
  { clave: "vistas", etiqueta: "Vistas" },
  { clave: "retencion_3s", etiqueta: "Retención 3 s (%)" },
  { clave: "porcentaje_visto", etiqueta: "% visto" },
  { clave: "comentarios_vet", etiqueta: "Comentarios" },
  { clave: "mensajes_whatsapp", etiqueta: "Mensajes WhatsApp" },
  { clave: "gasto_ads", etiqueta: "Gasto ads (S/)" },
  { clave: "resultados_ads", etiqueta: "Resultados ads" },
];

export function VideoDetalle({ video, onClose, onSaved }: { video: Video; onClose: () => void; onSaved: () => void }) {
  const dialogo = useRef<HTMLDialogElement>(null);
  const [red, setRed] = useState<RedSocial>("instagram");
  const [copy, setCopy] = useState<CopyVideo>(video.copy ?? {});
  const [estado, setEstado] = useState<EstadoVideo>(video.estado);
  const [enlaces, setEnlaces] = useState(video.enlaces ?? {});
  const [metricas, setMetricas] = useState<MetricasVideo>(video.metricas ?? {});
  const [notas, setNotas] = useState(video.notas ?? "");
  const [guardando, setGuardando] = useState(false);
  const [carpeta, setCarpeta] = useState(video.carpeta_id ?? "");
  const [carpetas, setCarpetas] = useState<Carpeta[]>([]);

  useEffect(() => {
    dialogo.current?.showModal();
    createClient().from("carpetas").select("*").order("orden").then(({ data }) => setCarpetas((data as Carpeta[]) ?? []));
  }, []);

  async function guardar() {
    setGuardando(true);
    const supabase = createClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    const { error } = await supabase
      .from("videos")
      .update({
        copy,
        copy_editado: video.copy_editado || JSON.stringify(copy) !== JSON.stringify(video.copy ?? {}),
        estado,
        enlaces,
        metricas,
        notas: notas.trim() || null,
        carpeta_id: carpeta || null,
        actualizado_por: user?.id ?? null,
        updated_at: new Date().toISOString(),
      })
      .eq("id", video.id);
    setGuardando(false);
    if (error) return toast.error("No se pudo guardar: " + error.message);
    toast.success("Guardado");
    onSaved();
  }

  function copiar() {
    const t = red === "youtube" ? `${copy.youtube_titulo ?? ""}\n\n${copy.youtube ?? ""}` : (copy[red] ?? "");
    navigator.clipboard.writeText(t);
    toast.success("Copy copiado");
  }

  return (
    <dialog
      ref={dialogo}
      onClose={onClose}
      aria-labelledby="titulo-video"
      className="m-auto max-h-[94dvh] w-[min(1150px,96vw)] rounded-3xl p-0 text-navy shadow-alzada backdrop:bg-navy-hondo/70 backdrop:backdrop-blur-sm open:animate-aparecer"
    >
      <div className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b bg-white px-4 py-3">
        <h2 id="titulo-video" className="text-2xl tracking-wider">
          {video.codigo}
        </h2>
        <button type="button" className="btn-sec" onClick={() => dialogo.current?.close()} aria-label="Cerrar">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid gap-6 p-4 md:grid-cols-2 xl:grid-cols-[270px_270px_minmax(0,1fr)]">
        <div className="flex flex-col gap-2">
          {video.video_url ? (
            <video
              src={video.video_url}
              poster={video.portada_url ?? undefined}
              controls
              playsInline
              preload="metadata"
              className="mx-auto aspect-[9/16] w-full max-w-[270px] rounded-lg bg-black"
            />
          ) : (
            <p className="text-sm text-slate-500">Sin video.</p>
          )}
          <div className="flex flex-wrap justify-center gap-2">
            {video.video_url && (
              <a className="btn-sec" href={video.video_url} download target="_blank" rel="noreferrer">
                <Download className="h-4 w-4" /> Video
              </a>
            )}
            {video.portada_url && (
              <a className="btn-sec" href={video.portada_url} download target="_blank" rel="noreferrer">
                <Download className="h-4 w-4" /> Portada
              </a>
            )}
          </div>
          <p className="text-center text-xs text-slate-500">
            {[video.tipo_hook && `Hook ${video.tipo_hook}`, video.dia?.replace("_", " "), video.duracion && `${video.duracion} s`]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>

        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap justify-center gap-1.5" role="group" aria-label="Red social">
            {REDES.map((r) => (
              <button
                key={r.valor}
                type="button"
                aria-pressed={red === r.valor}
                onClick={() => setRed(r.valor)}
                className="chip"
              >
                {r.etiqueta}
              </button>
            ))}
          </div>
          <VistaTelefono red={red} copy={copy} portada={video.portada_url} />
          <button type="button" className="btn-sec" onClick={copiar}>
            <Copy className="h-4 w-4" /> Copiar copy de {REDES.find((r) => r.valor === red)?.etiqueta}
          </button>
        </div>

        <div className="flex min-w-0 flex-col gap-4 md:col-span-2 xl:col-span-1">
          <label className="grid gap-1 text-sm font-medium">
            Copy Instagram
            <textarea rows={5} className="campo font-normal" value={copy.instagram ?? ""} onChange={(e) => setCopy({ ...copy, instagram: e.target.value })} />
            <span className="text-right text-xs font-normal text-slate-500">{(copy.instagram ?? "").length} / 2200</span>
          </label>
          <label className="grid gap-1 text-sm font-medium">
            Copy TikTok
            <textarea rows={4} className="campo font-normal" value={copy.tiktok ?? ""} onChange={(e) => setCopy({ ...copy, tiktok: e.target.value })} />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="grid gap-1 text-sm font-medium">
              Título YouTube
              <input className="campo font-normal" maxLength={100} value={copy.youtube_titulo ?? ""} onChange={(e) => setCopy({ ...copy, youtube_titulo: e.target.value })} />
            </label>
            <label className="grid gap-1 text-sm font-medium">
              Estado
              <select className="campo font-normal" value={estado} onChange={(e) => setEstado(e.target.value as EstadoVideo)}>
                {ESTADOS.map((e) => (
                  <option key={e.valor} value={e.valor}>
                    {e.etiqueta}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="grid gap-1 text-sm font-medium">
            Carpeta
            <select className="campo font-normal" value={carpeta} onChange={(e) => setCarpeta(e.target.value)}>
              <option value="">Sin carpeta (solo su sesión)</option>
              {carpetas.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.nombre}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1 text-sm font-medium">
            Descripción YouTube
            <textarea rows={3} className="campo font-normal" value={copy.youtube ?? ""} onChange={(e) => setCopy({ ...copy, youtube: e.target.value })} />
          </label>

          <fieldset className="grid gap-2 rounded-lg border bg-white p-3">
            <legend className="px-1 text-sm font-semibold">Dónde se publicó</legend>
            {REDES.map((r) => (
              <label key={r.valor} className="grid gap-1 text-sm sm:grid-cols-[90px_1fr] sm:items-center">
                {r.etiqueta}
                <input
                  type="url"
                  placeholder="https://…"
                  className="campo"
                  value={enlaces[r.valor] ?? ""}
                  onChange={(e) => setEnlaces({ ...enlaces, [r.valor]: e.target.value })}
                />
              </label>
            ))}
          </fieldset>

          <fieldset className="grid gap-2 rounded-lg border bg-white p-3">
            <legend className="px-1 text-sm font-semibold">Métricas</legend>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {CAMPOS_METRICAS.map((c) => (
                <label key={c.clave} className="grid gap-1 text-xs font-medium text-slate-600">
                  {c.etiqueta}
                  <input
                    type="number"
                    min={0}
                    step="any"
                    className="campo text-slate-900"
                    value={metricas[c.clave] ?? ""}
                    onChange={(e) => setMetricas({ ...metricas, [c.clave]: e.target.value === "" ? undefined : Number(e.target.value) })}
                  />
                </label>
              ))}
            </div>
          </fieldset>

          <label className="grid gap-1 text-sm font-medium">
            Notas
            <textarea rows={2} className="campo font-normal" value={notas} onChange={(e) => setNotas(e.target.value)} />
          </label>

          <div className="flex justify-end gap-2">
            <button type="button" className="btn-sec" onClick={() => dialogo.current?.close()}>
              Cancelar
            </button>
            <button type="button" className="btn" onClick={guardar} disabled={guardando}>
              {guardando ? "Guardando…" : "Guardar"}
            </button>
          </div>
        </div>
      </div>
    </dialog>
  );
}
