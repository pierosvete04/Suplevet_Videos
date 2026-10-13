import { Heart, MessageCircle, Send } from "lucide-react";
import { CORTE_COPY, type CopyVideo, type RedSocial } from "@/lib/videos";

// Cómo se ve el video en el feed antes de publicarlo: portada de fondo, usuario
// y copy cortado donde cada red pone "más".
const USUARIO: Record<RedSocial, string> = { instagram: "suplevet.pe", tiktok: "@suplevet", youtube: "@Suplevet" };
const ARRIBA: Record<RedSocial, string> = { instagram: "Reels", tiktok: "Para ti", youtube: "Shorts" };

export function VistaTelefono({ red, copy, portada }: { red: RedSocial; copy: CopyVideo; portada: string | null }) {
  const texto = red === "youtube" ? "" : ((red === "instagram" ? copy.instagram : copy.tiktok) ?? "");
  const corte = red === "youtube" ? 0 : CORTE_COPY[red];
  const largo = texto.length > corte;

  return (
    <div
      className="relative mx-auto aspect-[9/16] w-full max-w-[260px] overflow-hidden rounded-[28px] border-[7px] border-neutral-900 bg-black bg-cover bg-center text-white"
      style={portada ? { backgroundImage: `url('${portada}')` } : undefined}
    >
      <div className="absolute inset-0 bg-gradient-to-b from-transparent from-50% to-black/80" />
      <span className="absolute left-3 top-2.5 text-sm font-bold">{ARRIBA[red]}</span>
      <div className="absolute bottom-20 right-2 flex flex-col items-center gap-4" aria-hidden="true">
        <Heart className="h-5 w-5" />
        <MessageCircle className="h-5 w-5" />
        <Send className="h-5 w-5" />
      </div>
      <div className="absolute bottom-3 left-3 right-11 text-[11.5px] leading-snug [text-shadow:0_1px_2px_#000]">
        <p className="font-bold">{USUARIO[red]}</p>
        {red === "youtube" ? (
          <p className="font-bold">{copy.youtube_titulo}</p>
        ) : (
          <p>
            {(largo ? texto.slice(0, corte).trim() : texto).replace(/\n+/g, " ")}
            {largo && <span className="opacity-70">… más</span>}
          </p>
        )}
      </div>
    </div>
  );
}
