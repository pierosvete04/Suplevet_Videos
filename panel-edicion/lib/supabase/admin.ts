import { createClient as createSupabaseClient } from "@supabase/supabase-js";

// Service role: salta RLS. Solo en el servidor (rutas /api/contenido/*).
export function createAdminClient() {
  const clave = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!clave) throw new Error("Falta SUPABASE_SERVICE_ROLE_KEY en las variables de entorno");
  return createSupabaseClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, clave, {
    auth: { autoRefreshToken: false, persistSession: false },
  });
}
