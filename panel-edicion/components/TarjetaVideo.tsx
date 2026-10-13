"use client";

import { Eye, GripVertical, Play } from "lucide-react";
import { ESTADOS, type Video } from "@/lib/videos";

interface Props {
  video: Video;
  indice: number;
  onAbrir: (v: Video) => void;
  arrastrable?: boolean;
}

// Tarjeta 9:16 con la portada. Al pasar el mouse sube un poco y muestra «Ver»; se puede arrastrar a una carpeta.
export function TarjetaVideo({ video: v, indice, onAbrir, arrastrable }: Props) {
  const info = ESTADOS.find((e) => e.valor === v.estado)!;
  return (
    <div style={{ ["--i" as string]: indice }}>
      <button
        type="button"
        onClick={() => onAbrir(v)}
        draggable={arrastrable}
        onDragStart={(e) => {
          e.dataTransfer.setData("text/video-id", v.id);
          e.dataTransfer.effectAllowed = "move";
        }}
        className="group block w-full cursor-pointer overflow-hidden rounded-2xl bg-white text-left shadow-tarjeta transition-all duration-300 hover:-translate-y-1 hover:shadow-alzada"
        aria-label={`Abrir ${v.codigo}`}
      >
        <div
          className="relative aspect-[9/16] bg-navy bg-cover bg-center"
          style={v.portada_url ? { backgroundImage: `url('${v.portada_url}')` } : undefined}
        >
          {!v.portada_url && <Play className="absolute inset-0 m-auto h-10 w-10 text-sky/60" aria-hidden="true" />}
          <span className={`pastilla absolute left-2 top-2 ${info.clase}`}>{info.etiqueta}</span>
          {arrastrable && (
            <GripVertical className="absolute right-2 top-2 h-5 w-5 text-white/0 transition-colors group-hover:text-white/90" aria-hidden="true" />
          )}
          <span className="absolute inset-0 flex items-center justify-center bg-navy/0 transition-colors duration-300 group-hover:bg-navy/35">
            <span className="flex translate-y-2 items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-sm font-semibold text-navy opacity-0 transition-all duration-300 group-hover:translate-y-0 group-hover:opacity-100">
              <Eye className="h-4 w-4" aria-hidden="true" /> Ver
            </span>
          </span>
          <span className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-navy-hondo/95 to-transparent px-2.5 pb-2 pt-8 font-impacto text-[15px] tracking-wider text-white">
            {v.codigo}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 px-2.5 py-2 text-xs text-navy/60">
          {v.dia && <span>{v.dia.replace("_", " ")}</span>}
          {v.tipo_hook && <span>· {v.tipo_hook}</span>}
          {typeof v.metricas?.vistas === "number" && <span>· {v.metricas.vistas.toLocaleString("es-PE")} vistas</span>}
          {!v.dia && !v.tipo_hook && <span>{v.semana}</span>}
        </div>
      </button>
    </div>
  );
}
