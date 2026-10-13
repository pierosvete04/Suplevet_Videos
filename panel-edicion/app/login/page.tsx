"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

export default function LoginPage() {
  const router = useRouter();
  const [correo, setCorreo] = useState("");
  const [clave, setClave] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [entrando, setEntrando] = useState(false);

  async function entrar(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!correo.trim() || !clave) {
      setError("Escribe tu correo y tu contraseña.");
      return;
    }
    setEntrando(true);
    const { error } = await createClient().auth.signInWithPassword({ email: correo.trim(), password: clave });
    setEntrando(false);
    if (error) {
      setError("Correo o contraseña incorrectos.");
      return;
    }
    router.replace("/videos");
    router.refresh();
  }

  return (
    <main className="flex min-h-dvh items-center justify-center p-4">
      <form onSubmit={entrar} className="w-full max-w-sm rounded-2xl border bg-white p-6 shadow-sm" noValidate>
        <p className="text-xs font-bold uppercase tracking-widest text-naranja-tinta">Suplevet</p>
        <h1 className="mb-1 text-xl font-bold text-navy">Panel de edición</h1>
        <p className="mb-5 text-sm text-slate-600">Videos de la semana, portadas y copies.</p>
        <label htmlFor="correo" className="mb-1 block text-sm font-medium">
          Correo
        </label>
        <input id="correo" type="email" autoComplete="email" className="campo mb-3" value={correo} onChange={(e) => setCorreo(e.target.value)} />
        <label htmlFor="clave" className="mb-1 block text-sm font-medium">
          Contraseña
        </label>
        <input
          id="clave"
          type="password"
          autoComplete="current-password"
          className="campo mb-4"
          value={clave}
          onChange={(e) => setClave(e.target.value)}
        />
        {error && (
          <p role="alert" className="mb-3 text-sm text-red-700">
            {error}
          </p>
        )}
        <button className="btn w-full" disabled={entrando}>
          {entrando ? "Entrando…" : "Entrar"}
        </button>
      </form>
    </main>
  );
}
