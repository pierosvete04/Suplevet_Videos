import type { Metadata, Viewport } from "next";
import { Bebas_Neue, DM_Sans } from "next/font/google";
import { Toaster } from "sonner";
import "./globals.css";

// Tipografías oficiales de Suplevet: Bebas Neue (impacto) + DM Sans (cuerpo e interfaz).
const cuerpo = DM_Sans({ subsets: ["latin"], weight: ["400", "500", "700"], variable: "--fuente-cuerpo", display: "swap" });
const impacto = Bebas_Neue({ subsets: ["latin"], weight: "400", variable: "--fuente-impacto", display: "swap" });

export const metadata: Metadata = {
  title: "Edición Suplevet",
  description: "Videos, guiones, música y carpetas del equipo de contenido de Suplevet.",
  robots: { index: false, follow: false },
  icons: { icon: "/marca/icono-celeste.png" },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#253C61" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" className={`${cuerpo.variable} ${impacto.variable}`}>
      <body className="font-sans">
        {children}
        <Toaster position="top-center" richColors />
      </body>
    </html>
  );
}
