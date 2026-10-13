"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

// La base de datos lee el rol desde el «pase» de la sesión (JWT). Si al usuario le cambiaron el rol después de entrar,
// su pase todavía no lo trae: se renueva solo, sin obligarlo a cerrar sesión.
export function RenovarSesion({ rol }: { rol: string }) {
  const router = useRouter();
  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (session && session.user.app_metadata?.rol !== rol) {
        await supabase.auth.refreshSession();
        router.refresh();
        window.dispatchEvent(new Event("sesion-renovada"));
      }
    });
  }, [rol, router]);
  return null;
}
