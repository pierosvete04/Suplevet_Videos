import type { Config } from "tailwindcss";

// Paleta oficial de Suplevet (BRANDING SUPLEVET/_marca-comun/03_SISTEMA_DE_COLOR.md): 60 % blanco/gris,
// 25 % azul, 10 % celeste, 5 % naranja. El naranja nunca va como texto sobre blanco: para eso naranja-tinta.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: { DEFAULT: "#253C61", hondo: "#1B2C48" },
        sky: { DEFAULT: "#99D3DA", suave: "#E6F4F6" },
        naranja: { DEFAULT: "#EA8C43", hondo: "#C96A22", tinta: "#A8530F" },
        gris: "#F2F2F2",
      },
      fontFamily: {
        sans: ["var(--fuente-cuerpo)", "Inter", "Helvetica", "Arial", "sans-serif"],
        impacto: ["var(--fuente-impacto)", "Oswald", "Arial Narrow", "sans-serif"],
      },
      boxShadow: {
        tarjeta: "0 1px 2px rgba(37,60,97,.06), 0 4px 16px rgba(37,60,97,.06)",
        alzada: "0 2px 4px rgba(37,60,97,.08), 0 12px 32px rgba(37,60,97,.14)",
      },
      keyframes: {
        aparecer: { from: { opacity: "0", transform: "translateY(12px) scale(.98)" }, to: { opacity: "1", transform: "none" } },
        flotar: { "0%,100%": { transform: "translateY(0) rotate(var(--giro,0deg))" }, "50%": { transform: "translateY(-6px) rotate(var(--giro,0deg))" } },
        barrido: { from: { backgroundPosition: "200% 0" }, to: { backgroundPosition: "-200% 0" } },
      },
      animation: {
        aparecer: "aparecer .42s cubic-bezier(.2,.8,.2,1.15) both",
        flotar: "flotar 7s ease-in-out infinite",
        barrido: "barrido 1.6s linear infinite",
      },
    },
  },
  plugins: [],
};
export default config;
