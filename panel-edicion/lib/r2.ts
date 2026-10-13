import { S3Client } from "@aws-sdk/client-s3";

// Server-only. R2 expone una API compatible con S3; "auto" es la región que pide Cloudflare.
function env(nombre: string): string {
  const v = process.env[nombre];
  if (!v) throw new Error(`Falta ${nombre} en las variables de entorno`);
  return v;
}

export function r2() {
  return {
    cliente: new S3Client({
      region: "auto",
      endpoint: `https://${env("CLOUDFLARE_ACCOUNT_ID")}.r2.cloudflarestorage.com`,
      credentials: { accessKeyId: env("R2_ACCESS_KEY_ID"), secretAccessKey: env("R2_SECRET_ACCESS_KEY") },
    }),
    bucket: env("R2_BUCKET_NAME"),
  };
}

export function r2PublicUrl(key: string): string {
  const base = env("R2_PUBLIC_URL");
  return `${/^https?:\/\//i.test(base) ? base : `https://${base.replace(/^\/+/, "")}`}/${key}`;
}

// Las claves llevan Date.now(): un video re-armado tiene URL nueva, así que el caché puede ser inmutable.
export const R2_CACHE_CONTROL = "public, max-age=31536000, immutable";

// Todo lo del panel vive bajo este prefijo, separado de los archivos de la web.
export const PREFIJO = "panel-videos";
