# Panel de edición Suplevet (edicion.suplevet.pe/videos)

App aparte de la web y del admin. Aquí se revisan los videos que arma la sala de edición: portada, vista previa por red, copy, estado, enlaces y métricas.

- **Base de datos:** Supabase, proyecto `SUPLEVET_VIDEOS` (`brrwowhigjakbhzqjlky`, São Paulo, plan gratis). Tablas `miembros` (quién entra) y `videos`.
- **Archivos:** Cloudflare R2, carpeta `panel-videos/` del bucket. Al re-subir un video se borra la versión anterior.
- **Acceso:** correo y contraseña de Supabase Auth. Solo entra quien esté en `miembros` con `activo = true`.

## Variables de entorno

Ver `.env.example`. En Hostinger se cargan en la configuración de la app Node.js.

| Variable | De dónde sale |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase → SUPLEVET_VIDEOS → Project Settings → API Keys (URL y clave publishable) |
| `SUPABASE_SERVICE_ROLE_KEY` | Misma pantalla, clave **secret** / service_role. No se comparte nunca. |
| `CLOUDFLARE_ACCOUNT_ID`, `R2_*` | Las mismas de la web (`.env.local` de PÁGINA OFICIAL) |
| `CONTENIDO_TOKEN` | Texto aleatorio de 32+ caracteres. El mismo va en `editor/config.local.json` → `panel.token` de cada PC con la sala |

## Usuarios

El acceso no usa tablas: el rol vive en la propia cuenta de Supabase Auth (`app_metadata.rol` = `admin` o `editor`), que solo el servidor puede cambiar. Una cuenta sin rol no ve nada.

1. Supabase → Authentication → Sign In / Providers → desactivar **Allow new users to sign up**.
2. Authentication → Users → **Add user** → correo y contraseña, con *Auto Confirm User*.
3. Darle el rol (SQL Editor, o pídeselo a Claude):

```sql
update auth.users
set raw_app_meta_data = raw_app_meta_data || '{"rol": "editor", "nombre": "Benito"}'
where email = 'benito.svete@suplevet.pe';
```

La persona tiene que cerrar sesión y volver a entrar para que el rol se aplique. Para quitar el acceso, borra `rol` o pon `"rol": "ninguno"`.

## Publicar en Hostinger (subdominio edicion.suplevet.pe)

1. hPanel → Dominios → crear el subdominio `edicion.suplevet.pe`.
2. hPanel → Websites → **Node.js** → nueva app en ese subdominio, Node 20 o superior.
3. Subir este proyecto (GitHub o zip sin `node_modules` ni `.next`).
4. Comandos: build `npm install && npm run build`, start `npm start`.
5. Cargar las variables de entorno y reiniciar.
6. En la sala de edición (`editor/config.local.json`): `"panel": {"url": "https://edicion.suplevet.pe", "token": "<CONTENIDO_TOKEN>"}`.

## Desarrollo local

```bash
npm install
npm run dev
```

Abre http://localhost:3100. Necesita `.env.local` (no se sube a git).
