"use client";

import { useEffect, useMemo, useState } from "react";
import { Copy, Image as ImageIcon, MonitorPlay, PenLine, Video as VideoIcon } from "lucide-react";
import { toast } from "sonner";
import { createClient } from "@/lib/supabase/client";
import { piezasParaGrabar, type Guion } from "@/lib/guion";

const ETIQUETA = { cuerpo: "Cuerpo", hook: "Hook", cta: "CTA" } as const;
const ESTILO = { cuerpo: "bg-navy text-white", hook: "bg-sky text-navy", cta: "bg-naranja text-navy" } as const;

function copiar(texto: string, que: string) {
  navigator.clipboard.writeText(texto);
  toast.success(`${que} copiado`);
}

export default function CreadorPage() {
  const [guiones, setGuiones] = useState<Guion[]>([]);
  const [sesion, setSesion] = useState("");
  const [cargando, setCargando] = useState(true);

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
        setSesion((s) => s || g[0]?.sesion || "");
        setCargando(false);
      });
  }, [version]);

  const guion = guiones.find((g) => g.sesion === sesion);
  const piezas = useMemo(() => (guion ? piezasParaGrabar(guion.datos) : []), [guion]);
  const assets = guion?.datos.assets ?? [];

  return (
    <div className="flex flex-col gap-6">
      <section className="tarjeta relative z-10 flex flex-wrap items-end justify-between gap-4 p-5 sm:p-6">
        <div>
          <p className="etiqueta">Antes de grabar</p>
          <h1 className="text-4xl leading-none sm:text-5xl">Creador</h1>
          <p className="mt-1 max-w-2xl text-sm text-navy/70">
            El guion de cada sesión en el orden en que se graba, con los prompts de Google Flow para las imágenes. Ábrelo en el
            teleprompter desde la tablet: aquí sí sigue tu voz.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Sesión" className="campo w-auto min-w-[11rem]" value={sesion} onChange={(e) => setSesion(e.target.value)}>
            {guiones.map((g) => (
              <option key={g.sesion}>{g.sesion}</option>
            ))}
          </select>
          {guion && (
            <a className="btn-acento" href={`/teleprompter.html?sesion=${encodeURIComponent(sesion)}`} target="_blank" rel="noreferrer">
              <MonitorPlay className="h-4 w-4" aria-hidden="true" /> Teleprompter
            </a>
          )}
        </div>
      </section>

      {!cargando && !guion && (
        <div className="tarjeta flex flex-col items-center gap-3 p-12 text-center">
          <span className="flex h-16 w-16 items-center justify-center rounded-2xl bg-sky-suave">
            <PenLine className="h-8 w-8" aria-hidden="true" />
          </span>
          <h2 className="text-2xl">Todavía no hay guiones</h2>
          <p className="max-w-md text-sm text-navy/70">
            Pídele a Claude el guion de la próxima sesión. La sala lo sube aquí con sus piezas, los prompts de Flow y los de música.
          </p>
        </div>
      )}

      {guion && (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          <section aria-label="Piezas" className="cascada flex flex-col gap-3">
            {piezas.map((p, i) => (
              <article key={p.nombre} style={{ ["--i" as string]: i }} className="tarjeta p-4">
                <header className="mb-2 flex flex-wrap items-center gap-2">
                  <span className={`pastilla ${ESTILO[p.tipo]}`}>{ETIQUETA[p.tipo]}</span>
                  <h2 className="text-2xl leading-none">{p.nombre}</h2>
                  {p.tipo_hook && <span className="text-sm text-navy/60">· {p.tipo_hook}</span>}
                  {p.info.portada && (
                    <span className="ml-auto rounded-lg bg-gris px-2 py-1 text-xs">
                      Portada: <b>{p.info.portada.replace(/\*/g, "")}</b>
                    </span>
                  )}
                </header>
                {p.partes.length > 1 ? (
                  <ol className="grid gap-1.5">
                    {p.partes.map((frase, k) => (
                      <li key={k} className="flex gap-3 rounded-lg bg-gris/70 px-3 py-2 text-[15px] leading-relaxed">
                        <span className="font-impacto text-lg leading-6 text-naranja-tinta">{k + 1}</span>
                        {frase}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-[15px] leading-relaxed">{p.texto}</p>
                )}
                {!!p.info.marcas?.length && (
                  <p className="mt-2 flex flex-wrap gap-1.5 text-xs text-navy/60">
                    {p.info.marcas.map((m, k) => (
                      <span key={k} className="rounded-full border border-navy/15 px-2 py-0.5">
                        «{m.palabra}» → {m.asset ? "imagen" : m.sub ? m.sub.replace("_", " ") : m.musica ? "música" : m.sfx}
                      </span>
                    ))}
                  </p>
                )}
              </article>
            ))}
          </section>

          <aside className="flex flex-col gap-3">
            <h2 className="text-2xl">Imágenes y clips de Flow</h2>
            {assets.length === 0 && <p className="tarjeta p-4 text-sm text-navy/60">Este guion no pide imágenes.</p>}
            {assets.map((a, i) => (
              <article key={a.id} style={{ ["--i" as string]: i }} className="tarjeta animate-aparecer p-4">
                <div className="mb-1 flex items-center gap-2">
                  {a.tipo === "clip" ? <VideoIcon className="h-4 w-4 text-naranja-tinta" aria-hidden="true" /> : <ImageIcon className="h-4 w-4 text-naranja-tinta" aria-hidden="true" />}
                  <span className="font-impacto text-lg tracking-wider">{a.id}</span>
                </div>
                <p className="text-xs text-navy/60">
                  {a.pieza} · en «{a.palabra}» · {a.flow?.formato}
                </p>
                <p className="mt-2 text-sm leading-relaxed">{a.flow?.prompt}</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button type="button" className="btn-sec" onClick={() => copiar(a.flow?.prompt ?? "", "Prompt")}>
                    <Copy className="h-4 w-4" aria-hidden="true" /> Prompt
                  </button>
                  {a.flow?.evitar && (
                    <button type="button" className="btn-sec" onClick={() => copiar(a.flow?.evitar ?? "", "Evitar")}>
                      <Copy className="h-4 w-4" aria-hidden="true" /> Evitar
                    </button>
                  )}
                  <button type="button" className="btn-sec" onClick={() => copiar(a.id, "Nombre")}>
                    <Copy className="h-4 w-4" aria-hidden="true" /> Nombre
                  </button>
                </div>
              </article>
            ))}
          </aside>
        </div>
      )}
    </div>
  );
}
