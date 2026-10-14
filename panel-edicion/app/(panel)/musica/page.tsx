"use client";

import { useEffect, useMemo, useState } from "react";
import { Copy, Music2, Sparkles, Wand2 } from "lucide-react";
import { toast } from "sonner";
import { createClient } from "@/lib/supabase/client";
import { textoPrompt, type Guion } from "@/lib/guion";

// Generador de prompts para Google Flow Music (o Suno): siempre instrumental y pensado como fondo de voz.
const TONOS = [
  { id: "veterinarios", nombre: "Veterinarios · confiable", base: "warm corporate acoustic track", animo: "hopeful and trustworthy", bpm: 98 },
  { id: "duenos", nombre: "Dueños · alegre juvenil", base: "upbeat modern pop", animo: "playful and positive", bpm: 120 },
  { id: "tension", nombre: "Tensión · hook de problema", base: "minimal cinematic pulse", animo: "curious and slightly tense", bpm: 90 },
  { id: "emotivo", nombre: "Emotivo · historia", base: "gentle emotional piano piece", animo: "tender and warm", bpm: 80 },
  { id: "energia", nombre: "Energía · oferta / CTA", base: "bright energetic electro-pop", animo: "confident and uplifting", bpm: 124 },
];
const INSTRUMENTOS = ["soft piano", "plucked guitar", "bright plucks", "claps", "light shaker", "punchy bass", "warm pads", "marimba", "strings", "lo-fi drums", "synth bass", "whistle hook"];
const EVITAR = "vocals, choir, rap, heavy distortion, sudden drops, long intro";

function copiar(texto: string, que: string) {
  navigator.clipboard.writeText(texto);
  toast.success(`${que} copiado`);
}

export default function MusicaPage() {
  const [guiones, setGuiones] = useState<Guion[]>([]);
  const [sesion, setSesion] = useState("todas");
  const [tono, setTono] = useState(TONOS[0]);
  const [bpm, setBpm] = useState(TONOS[0].bpm);
  const [inst, setInst] = useState<string[]>(["soft piano", "plucked guitar", "light shaker"]);
  const [extra, setExtra] = useState("");

  const [version, setVersion] = useState(0);
  useEffect(() => {
    const r = () => setVersion((v) => v + 1);
    window.addEventListener("sesion-renovada", r);
    return () => window.removeEventListener("sesion-renovada", r);
  }, []);

  useEffect(() => {
    createClient()
      .from("guiones")
      .select("*")
      .order("updated_at", { ascending: false })
      .then(({ data }) => {
        const g = (data as Guion[]) ?? [];
        setGuiones(g);
        setSesion((s) => (s === "todas" && g.length > 1 ? g[0].sesion : s)); // por defecto, solo el guion más reciente
      });
  }, [version]);

  const pistas = useMemo(
    () =>
      guiones.filter((g) => sesion === "todas" || g.sesion === sesion).flatMap((g) =>
        (g.datos.musica ?? []).map((m) => ({
          ...m,
          sesion: g.sesion,
          usan: Object.entries(g.datos.piezas ?? {})
            .filter(([, p]) => p.musica === m.id || p.marcas?.some((x) => x.musica === m.id))
            .map(([n]) => n),
        }))
      ),
    [guiones, sesion]
  );

  const prompt = `${tono.base[0].toUpperCase()}${tono.base.slice(1)} at ${bpm} BPM with ${inst.slice(0, 4).join(", ").replace(/, ([^,]*)$/, " and $1")}, ${tono.animo}${extra.trim() ? `, ${extra.trim()}` : ""}, instrumental, no vocals, background music for voiceover, steady energy, no sudden drops`;

  return (
    <div className="flex flex-col gap-6">
      <section className="tarjeta relative z-10 p-5 sm:p-6">
        <p className="etiqueta">Google Flow Music · Suno</p>
        <h1 className="text-4xl leading-none sm:text-5xl">Música</h1>
        <p className="mt-1 max-w-2xl text-sm text-navy/70">
          Una pista por cuerpo y solo cambia donde suma (el CTA o un giro). Copia el prompt, genérala instrumental y arrástrala en la sala
          (Faltantes → Música) con su nombre.
        </p>
      </section>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <section aria-label="Pistas de los guiones" className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-2xl">Pistas de los guiones</h2>
            {guiones.length > 1 && (
              <select aria-label="Sesión" className="campo w-auto min-w-[11rem]" value={sesion} onChange={(e) => setSesion(e.target.value)}>
                <option value="todas">Todas las sesiones</option>
                {guiones.map((g) => (
                  <option key={g.sesion}>{g.sesion}</option>
                ))}
              </select>
            )}
          </div>
          {pistas.length === 0 && (
            <div className="tarjeta flex flex-col items-center gap-2 p-10 text-center text-sm text-navy/60">
              <Music2 className="h-8 w-8 text-sky" aria-hidden="true" />
              Cuando Claude escriba un guion, sus pistas aparecen aquí con el prompt listo.
            </div>
          )}
          <div className="cascada grid gap-3 md:grid-cols-2">
            {pistas.map((p, i) => (
              <article key={`${p.sesion}-${p.id}`} style={{ ["--i" as string]: i }} className="tarjeta flex flex-col gap-2 p-4">
                <div className="flex items-center gap-2">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-navy text-sky">
                    <Music2 className="h-5 w-5" aria-hidden="true" />
                  </span>
                  <div className="min-w-0">
                    <p className="truncate font-impacto text-xl leading-none tracking-wider">{p.id}</p>
                    <p className="text-xs text-navy/60">{p.sesion} · {p.prompt?.herramienta ?? (p.suno ? "Suno" : "Google Flow Music")}</p>
                  </div>
                </div>
                {p.uso && <p className="text-sm"><b>Para:</b> {p.uso}</p>}
                {p.usan.length > 0 && (
                  <p className="flex flex-wrap gap-1">
                    {p.usan.map((n) => (
                      <span key={n} className="pastilla bg-sky-suave text-navy">{n}</span>
                    ))}
                  </p>
                )}
                <p className="rounded-xl bg-gris p-3 text-sm leading-relaxed">{textoPrompt(p)}</p>
                <div className="mt-auto flex flex-wrap gap-2">
                  <button type="button" className="btn-sec" onClick={() => copiar(textoPrompt(p), "Prompt")}>
                    <Copy className="h-4 w-4" aria-hidden="true" /> Prompt
                  </button>
                  <button type="button" className="btn-sec" onClick={() => copiar(p.prompt?.evitar ?? EVITAR, "Evitar")}>
                    <Copy className="h-4 w-4" aria-hidden="true" /> Evitar
                  </button>
                  <button type="button" className="btn-sec" onClick={() => copiar(p.id, "Nombre")}>
                    <Copy className="h-4 w-4" aria-hidden="true" /> Nombre
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>

        <aside className="tarjeta sticky top-4 flex h-fit flex-col gap-4 overflow-hidden p-0">
          <div className="bg-gradient-to-br from-navy to-navy-hondo p-5 text-white">
            <p className="flex items-center gap-2 font-impacto text-2xl tracking-wider">
              <Wand2 className="h-5 w-5 text-naranja" aria-hidden="true" /> Generador de prompt
            </p>
            <p className="text-sm text-white/75">Arma una pista nueva para un video que no tiene guion.</p>
          </div>
          <div className="flex flex-col gap-4 px-5 pb-5">
            <fieldset className="grid gap-1.5">
              <legend className="mb-1 text-sm font-semibold">Tono</legend>
              {TONOS.map((t) => (
                <label key={t.id} className={`flex min-h-11 cursor-pointer items-center gap-2 rounded-xl border px-3 text-sm transition-colors ${tono.id === t.id ? "border-navy bg-sky-suave" : "border-navy/15 hover:bg-gris"}`}>
                  <input type="radio" name="tono" className="accent-navy" checked={tono.id === t.id} onChange={() => { setTono(t); setBpm(t.bpm); }} />
                  {t.nombre}
                </label>
              ))}
            </fieldset>
            <label className="grid gap-1 text-sm font-semibold">
              Velocidad: {bpm} BPM
              <input type="range" min={70} max={140} value={bpm} onChange={(e) => setBpm(Number(e.target.value))} className="accent-naranja" />
            </label>
            <fieldset>
              <legend className="mb-1.5 text-sm font-semibold">Instrumentos (hasta 4)</legend>
              <div className="flex flex-wrap gap-1.5">
                {INSTRUMENTOS.map((x) => (
                  <button key={x} type="button" className="chip" aria-pressed={inst.includes(x)}
                    onClick={() => setInst((v) => (v.includes(x) ? v.filter((y) => y !== x) : v.length >= 4 ? v : [...v, x]))}>
                    {x}
                  </button>
                ))}
              </div>
            </fieldset>
            <label className="grid gap-1 text-sm font-semibold">
              Algo más (en inglés, opcional)
              <input className="campo font-normal" placeholder="e.g. subtle sunrise feeling" value={extra} onChange={(e) => setExtra(e.target.value)} />
            </label>
            <div className="rounded-xl border-2 border-dashed border-sky bg-sky-suave/50 p-3 text-sm leading-relaxed" aria-live="polite">{prompt}</div>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-acento flex-1" onClick={() => copiar(prompt, "Prompt")}>
                <Sparkles className="h-4 w-4" aria-hidden="true" /> Copiar prompt
              </button>
              <button type="button" className="btn-sec" onClick={() => copiar(EVITAR, "Evitar")}>
                <Copy className="h-4 w-4" aria-hidden="true" /> Evitar
              </button>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
