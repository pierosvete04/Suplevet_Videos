// Tipos y cálculos del panel de videos.
export type EstadoVideo = "borrador" | "aprobado" | "publicado" | "descartado";
export type RedSocial = "instagram" | "tiktok" | "youtube";

export interface CopyVideo {
  instagram?: string;
  tiktok?: string;
  youtube_titulo?: string;
  youtube?: string;
}

export interface MetricasVideo {
  vistas?: number;
  retencion_3s?: number;
  porcentaje_visto?: number;
  comentarios_vet?: number;
  mensajes_whatsapp?: number;
  gasto_ads?: number;
  resultados_ads?: number;
}

export interface Video {
  id: string;
  semana: string;
  codigo: string;
  hook: string | null;
  cuerpo: string | null;
  cta: string | null;
  tipo_hook: string | null;
  dia: string | null;
  titulo_portada: string | null;
  duracion: number | null;
  video_url: string | null;
  portada_url: string | null;
  copy: CopyVideo;
  copy_editado: boolean;
  estado: EstadoVideo;
  enlaces: Partial<Record<RedSocial, string>>;
  metricas: MetricasVideo;
  notas: string | null;
  updated_at: string;
}

export const ESTADOS: { valor: EstadoVideo; etiqueta: string; clase: string }[] = [
  { valor: "borrador", etiqueta: "Borrador", clase: "bg-slate-200 text-slate-700" },
  { valor: "aprobado", etiqueta: "Aprobado", clase: "bg-blue-100 text-blue-800" },
  { valor: "publicado", etiqueta: "Publicado", clase: "bg-green-100 text-green-800" },
  { valor: "descartado", etiqueta: "Descartado", clase: "bg-red-100 text-red-800" },
];

export const REDES: { valor: RedSocial; etiqueta: string }[] = [
  { valor: "instagram", etiqueta: "Instagram" },
  { valor: "tiktok", etiqueta: "TikTok" },
  { valor: "youtube", etiqueta: "Shorts" },
];

// Caracteres que se ven antes del "más" (aprox.). Solo para la vista previa.
export const CORTE_COPY = { instagram: 125, tiktok: 100 } as const;

/** "C1_H3" -> "H3". Los hooks de la misma posición son del mismo tipo en toda la semana. */
export function posicionHook(v: Pick<Video, "hook" | "codigo">): string {
  return (v.hook ?? v.codigo).match(/H\d+/)?.[0] ?? "—";
}

export interface FilaRanking {
  grupo: string;
  videos: number;
  vistas: number;
  retencion3s: number | null;
  contactos: number;
  resultadosAds: number;
  costoPorResultado: number | null;
}

/** Agrupa por hook, cuerpo, CTA o tipo de hook para ver qué pieza gana. */
export function ranking(videos: Video[], agrupar: (v: Video) => string): FilaRanking[] {
  const grupos = new Map<string, Video[]>();
  for (const v of videos) grupos.set(agrupar(v), [...(grupos.get(agrupar(v)) ?? []), v]);
  return [...grupos.entries()]
    .map(([grupo, vs]) => {
      const conRet = vs.filter((v) => typeof v.metricas.retencion_3s === "number");
      const suma = (k: keyof MetricasVideo) => vs.reduce((s, v) => s + (v.metricas[k] ?? 0), 0);
      const resultados = suma("resultados_ads");
      return {
        grupo,
        videos: vs.length,
        vistas: suma("vistas"),
        retencion3s: conRet.length ? conRet.reduce((s, v) => s + (v.metricas.retencion_3s ?? 0), 0) / conRet.length : null,
        contactos: suma("comentarios_vet") + suma("mensajes_whatsapp"),
        resultadosAds: resultados,
        costoPorResultado: resultados > 0 ? suma("gasto_ads") / resultados : null,
      };
    })
    .sort((a, b) => a.grupo.localeCompare(b.grupo));
}
