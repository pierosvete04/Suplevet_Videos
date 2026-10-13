import { NextResponse } from "next/server";
import { PutObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { z } from "zod";
import { PREFIJO, r2, r2PublicUrl, R2_CACHE_CONTROL } from "@/lib/r2";
import { tokenValido } from "@/lib/token";

// Paso 1 de la subida desde la sala: una URL firmada de R2 por archivo (video y
// portada). La sala hace el PUT directo a R2 y luego llama a /registrar.
const bodySchema = z.object({
  semana: z.string().regex(/^[A-Za-z0-9_-]{1,40}$/),
  archivos: z
    .array(z.object({ nombre: z.string().regex(/^[A-Za-z0-9._-]{1,120}$/), tipo: z.enum(["video/mp4", "image/jpeg"]) }))
    .min(1)
    .max(4),
});

export async function POST(request: Request) {
  if (!tokenValido(request)) return NextResponse.json({ error: "No autorizado" }, { status: 401 });
  const parsed = bodySchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: "Datos inválidos" }, { status: 400 });

  const { cliente, bucket } = r2();
  const subidas = await Promise.all(
    parsed.data.archivos.map(async ({ nombre, tipo }) => {
      const key = `${PREFIJO}/${parsed.data.semana}/${Date.now()}-${nombre}`;
      const uploadUrl = await getSignedUrl(
        cliente,
        new PutObjectCommand({ Bucket: bucket, Key: key, ContentType: tipo, CacheControl: R2_CACHE_CONTROL }),
        { expiresIn: 900 }
      );
      return { nombre, tipo, key, uploadUrl, publicUrl: r2PublicUrl(key), cacheControl: R2_CACHE_CONTROL };
    })
  );
  return NextResponse.json({ subidas });
}
