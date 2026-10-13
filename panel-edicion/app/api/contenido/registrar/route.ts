import { NextResponse } from "next/server";
import { DeleteObjectsCommand } from "@aws-sdk/client-s3";
import { z } from "zod";
import { createAdminClient } from "@/lib/supabase/admin";
import { r2 } from "@/lib/r2";
import { tokenValido } from "@/lib/token";

// Paso 2 de la subida: guarda o actualiza la fila del video. El copy corregido
// en el panel (copy_editado) no se pisa; estado, enlaces y métricas nunca se
// tocan desde aquí. Si el video se re-armó, se borran de R2 los archivos viejos
// para no llenar los 10 GB gratis.
const texto = (max: number) => z.string().max(max).nullish();
const videoSchema = z.object({
  semana: z.string().regex(/^[A-Za-z0-9_-]{1,40}$/),
  codigo: z.string().regex(/^[A-Za-z0-9_-]{1,80}$/),
  hook: texto(40),
  cuerpo: texto(40),
  cta: texto(40),
  tipo_hook: texto(40),
  dia: texto(20),
  titulo_portada: texto(200),
  duracion: z.number().nonnegative().max(600).nullish(),
  video_url: z.string().url().max(500),
  portada_url: z.string().url().max(500).nullish(),
  r2_keys: z.array(z.string().max(300)).max(4),
  copy: z
    .object({ instagram: texto(2200), tiktok: texto(4000), youtube_titulo: texto(100), youtube: texto(5000) })
    .partial(),
});
const bodySchema = z.object({ videos: z.array(videoSchema).min(1).max(20) });

export async function POST(request: Request) {
  if (!tokenValido(request)) return NextResponse.json({ error: "No autorizado" }, { status: 401 });
  const parsed = bodySchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: "Datos inválidos" }, { status: 400 });

  const supabase = createAdminClient();
  const { data: existentes, error: errLectura } = await supabase
    .from("videos")
    .select("codigo, copy_editado, r2_keys")
    .in("codigo", parsed.data.videos.map((v) => v.codigo));
  if (errLectura) return NextResponse.json({ error: errLectura.message }, { status: 500 });
  const previo = new Map((existentes ?? []).map((e) => [e.codigo as string, e]));

  const filas = parsed.data.videos.map(({ copy, ...v }) => ({
    ...v,
    ...(previo.get(v.codigo)?.copy_editado ? {} : { copy }),
    updated_at: new Date().toISOString(),
  }));
  const { error } = await supabase.from("videos").upsert(filas, { onConflict: "codigo" });
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  const viejas = parsed.data.videos.flatMap((v) =>
    ((previo.get(v.codigo)?.r2_keys as string[] | undefined) ?? []).filter((k) => !v.r2_keys.includes(k))
  );
  if (viejas.length > 0) {
    const { cliente, bucket } = r2();
    await cliente
      .send(new DeleteObjectsCommand({ Bucket: bucket, Delete: { Objects: viejas.map((Key) => ({ Key })) } }))
      .catch(() => undefined); // si falla, solo queda un archivo huérfano
  }
  return NextResponse.json({ ok: true, guardados: filas.length });
}
