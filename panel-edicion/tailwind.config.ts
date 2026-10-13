import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: "#253C61",
        naranja: "#EA8C43",
        "naranja-tinta": "#A8530F",
        fondo: "#F2F4F6",
      },
    },
  },
  plugins: [],
};
export default config;
