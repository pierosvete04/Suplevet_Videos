"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Clapperboard, FolderOpen, LogOut, Music2, PenLine } from "lucide-react";

const MENU = [
  { href: "/videos", etiqueta: "Videos", icono: Clapperboard, ayuda: "Portadas, copy y estado" },
  { href: "/carpetas", etiqueta: "Carpetas", icono: FolderOpen, ayuda: "Sesiones y carpetas propias" },
  { href: "/creador", etiqueta: "Creador", icono: PenLine, ayuda: "Guiones, prompts y teleprompter" },
  { href: "/musica", etiqueta: "Música", icono: Music2, ayuda: "Pistas y generador de prompts" },
];

// Huellitas decorativas: gris claro semitransparente, nunca el foco (05_SISTEMA_GRAFICO.md)
function Huella({ className, giro }: { className: string; giro: number }) {
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true" className={`absolute fill-white/[.07] animate-flotar ${className}`}
      style={{ ["--giro" as string]: `${giro}deg` }}>
      <ellipse cx="17" cy="22" rx="6" ry="8" />
      <ellipse cx="29" cy="14" rx="6" ry="8" />
      <ellipse cx="41" cy="16" rx="6" ry="8" />
      <ellipse cx="50" cy="27" rx="5.5" ry="7.5" />
      <path d="M32 30c9 0 17 9 17 17 0 6-5 9-10 8-3-1-5-2-7-2s-4 1-7 2c-5 1-10-2-10-8 0-8 8-17 17-17z" />
    </svg>
  );
}

export function Encabezado({ nombre, rol }: { nombre: string; rol: string }) {
  const ruta = usePathname();

  return (
    <header className="relative overflow-hidden bg-gradient-to-b from-navy-hondo via-navy to-navy text-white">
      <Huella className="-left-4 top-6 h-28 w-28" giro={-18} />
      <Huella className="right-[12%] top-2 h-16 w-16" giro={22} />
      <Huella className="right-[38%] top-16 h-10 w-10" giro={-8} />

      <div className="relative mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 pt-4 sm:px-6">
        <Link href="/videos" className="flex items-center gap-3 rounded-lg" aria-label="Edición Suplevet, inicio">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/marca/logo-blanco.png" alt="Suplevet" width={150} height={32} className="h-7 w-auto sm:h-8" />
          <span className="hidden border-l border-white/25 pl-3 font-impacto text-xl tracking-[.14em] text-sky sm:inline">
            Edición
          </span>
        </Link>
        <div className="flex items-center gap-2 text-sm">
          <span className="hidden text-white/80 sm:inline">
            {nombre} · <span className="text-sky">{rol === "admin" ? "Administrador" : "Editor"}</span>
          </span>
          <form action="/salir" method="post">
            <button className="inline-flex h-11 w-11 cursor-pointer items-center justify-center rounded-xl text-white/80 transition-colors hover:bg-white/10 hover:text-white"
              aria-label="Cerrar sesión" title="Cerrar sesión">
              <LogOut className="h-5 w-5" />
            </button>
          </form>
        </div>
      </div>

      <nav aria-label="Secciones" className="relative mx-auto max-w-7xl overflow-x-auto px-4 pb-12 pt-5 sm:px-6">
        <ul className="flex min-w-max gap-1.5">
          {MENU.map(({ href, etiqueta, icono: Icono, ayuda }) => {
            const activo = ruta.startsWith(href);
            return (
              <li key={href}>
                <Link href={href} aria-current={activo ? "page" : undefined} title={ayuda}
                  className={`group flex min-h-11 items-center gap-2 rounded-xl px-4 text-[15px] font-semibold transition-all duration-200 ${
                    activo ? "bg-white text-navy shadow-alzada" : "text-white/80 hover:bg-white/10 hover:text-white"}`}>
                  <Icono className={`h-[18px] w-[18px] transition-transform duration-200 group-hover:-rotate-6 ${activo ? "text-naranja" : ""}`} />
                  {etiqueta}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* La colina: curva blanca convexa sobre el borde inferior del azul */}
      <div aria-hidden="true" className="colina absolute inset-x-0 bottom-0 h-10 bg-gris" />
    </header>
  );
}
