import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Portadas y videos vienen de R2 (pub-….r2.dev o dominio propio): se usan
  // con <img>/<video> normales, así que no hace falta configurar next/image.
  poweredByHeader: false,
};

export default nextConfig;
