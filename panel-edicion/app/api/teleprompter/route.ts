import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { piezasParaGrabar, type Guion } from "@/lib/guion";

// Mismo formato que el teleprompter de la sala: aquí, al ser https, la tablet puede usar el micrófono.
export async function GET(request: Request) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: "Sin sesión" }, { status: 401 });
  const { data } = await supabase.from("guiones").select("*").order("updated_at", { ascending: false });
  const guiones = (data as Guion[]) ?? [];
  const pedida = new URL(request.url).searchParams.get("sesion");
  const g = guiones.find((x) => x.sesion === pedida) ?? guiones[0];
  return NextResponse.json({
    sesion: g?.sesion ?? null,
    sesiones: guiones.map((x) => x.sesion),
    piezas: g ? piezasParaGrabar(g.datos).map(({ info: _info, ...p }) => p) : [],
  });
}
