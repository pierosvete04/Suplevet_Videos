import { createClient } from "@/lib/supabase/server";
import { Encabezado } from "@/components/Encabezado";
import { RenovarSesion } from "@/components/RenovarSesion";

// Todo lo que está dentro de (panel) comparte el encabezado azul con la colina y el menú.
// El middleware ya garantiza que hay sesión; el rol sale de la propia cuenta de Auth.
export default async function PanelLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  const rol = (user?.app_metadata?.rol as string | undefined) ?? null;
  const nombre = (user?.app_metadata?.nombre as string | undefined) || user?.email?.split("@")[0] || "";

  if (rol !== "admin" && rol !== "editor") {
    return (
      <main className="flex min-h-dvh items-center justify-center p-4 text-center">
        <div className="tarjeta max-w-sm p-6">
          <p className="mb-4 text-sm">
            Tu cuenta todavía no tiene un rol en el panel de edición. Pídele al administrador que te dé acceso; luego cierra sesión y
            vuelve a entrar.
          </p>
          <form action="/salir" method="post">
            <button className="btn-sec">Cerrar sesión</button>
          </form>
        </div>
      </main>
    );
  }

  return (
    <div className="min-h-dvh">
      <RenovarSesion rol={rol} />
      <Encabezado nombre={nombre} rol={rol} />
      <main className="mx-auto -mt-6 max-w-7xl px-4 pb-16 sm:px-6">{children}</main>
    </div>
  );
}
