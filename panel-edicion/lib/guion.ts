// El guion de una sesión tal como lo escribe Claude (produccion.json), guardado en la tabla `guiones`.

export interface PromptMusica {
  herramienta?: string;
  prompt?: string;
  evitar?: string;
  duracion?: string;
  [k: string]: string | undefined;
}

export interface PistaMusica {
  id: string;
  uso?: string;
  desde?: number;
  prompt?: PromptMusica;
  suno?: Record<string, string>;
}

export interface AssetFlow {
  id: string;
  tipo: "imagen" | "clip" | string;
  pieza?: string;
  palabra?: string;
  flow?: Record<string, string>;
}

export interface PiezaGuion {
  tipo?: string;
  texto?: string;
  partes?: string[];
  portada?: string;
  musica?: string;
  marcas?: { palabra: string; sub?: string; asset?: string; sfx?: string; musica?: string }[];
}

export interface DatosGuion {
  semana?: string;
  publico?: string;
  piezas?: Record<string, PiezaGuion>;
  assets?: AssetFlow[];
  musica?: PistaMusica[];
  copies?: { cuerpos?: Record<string, { texto?: string; hashtags?: string }>; hooks?: Record<string, string>; ctas?: Record<string, string> };
}

export interface Guion {
  sesion: string;
  datos: DatosGuion;
  updated_at: string;
}

export type TipoPieza = "cuerpo" | "hook" | "cta";

export function tipoPieza(nombre: string, info?: PiezaGuion): TipoPieza {
  if (info?.tipo === "cuerpo") return "cuerpo";
  const n = nombre.toUpperCase();
  return n.includes("CTA") ? "cta" : /_H\d+$/.test(n) ? "hook" : "cuerpo";
}

/** Piezas en el orden de la hoja de grabación: cuerpos (por frases), hooks y CTAs. */
export function piezasParaGrabar(datos: DatosGuion) {
  const orden = { cuerpo: 0, hook: 1, cta: 2 } as const;
  return Object.entries(datos.piezas ?? {})
    .filter(([, i]) => i.texto)
    .map(([nombre, i]) => {
      const tipo = tipoPieza(nombre, i);
      const partes = i.partes?.length ? i.partes : tipo === "cuerpo" ? (i.texto ?? "").split(/(?<=[.!?])\s+/) : [i.texto ?? ""];
      return { nombre, tipo, tipo_hook: tipo === "hook" ? i.tipo ?? null : null, texto: i.texto ?? "", partes: partes.filter((p) => p.trim()), info: i };
    })
    .sort((a, b) => orden[a.tipo] - orden[b.tipo] || a.nombre.localeCompare(b.nombre, "es", { numeric: true }));
}

/** Texto listo para pegar en Google Flow Music (o Suno). */
export function textoPrompt(p: PistaMusica): string {
  const pr = p.prompt ?? {};
  return pr.prompt ?? p.suno?.estilo ?? "";
}
