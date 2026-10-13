import { timingSafeEqual } from "node:crypto";

// La sala de edición corre en la PC del editor, sin sesión: se identifica con
// CONTENIDO_TOKEN, que solo abre /api/contenido/*. Sin la variable, las rutas quedan cerradas.
export function tokenValido(request: Request): boolean {
  const esperado = process.env.CONTENIDO_TOKEN;
  if (!esperado || esperado.length < 24) return false;
  const recibido = (request.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
  const a = Buffer.from(recibido);
  const b = Buffer.from(esperado);
  return a.length === b.length && timingSafeEqual(a, b);
}
