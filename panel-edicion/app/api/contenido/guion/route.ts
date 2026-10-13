import { NextResponse } from "next/server";
import { z } from "zod";
import { createAdminClient } from "@/lib/supabase/admin";
import { tokenValido } from "@/lib/token";

// La sala sube aquí el guion (produccion.json) de una sesión para el Creador, Música y el teleprompter.
const bodySchema = z.object({
  sesion: z.string().min(1).max(60),
  datos: z.record(z.string(), z.unknown()),
});

export async function POST(request: Request) {
  if (!tokenValido(request)) return NextResponse.json({ error: "No autorizado" }, { status: 401 });
  const parsed = bodySchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ error: "Datos inválidos" }, { status: 400 });
  if (JSON.stringify(parsed.data.datos).length > 500_000) return NextResponse.json({ error: "Guion demasiado grande" }, { status: 413 });
  const { error } = await createAdminClient()
    .from("guiones")
    .upsert({ sesion: parsed.data.sesion, datos: parsed.data.datos, updated_at: new Date().toISOString() }, { onConflict: "sesion" });
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  return NextResponse.json({ ok: true });
}
