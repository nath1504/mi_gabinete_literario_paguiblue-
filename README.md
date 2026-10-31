# Mi Gabinete Literario 🐧♡ — Paguiblue

Aplicación Flask para publicar novelas y capítulos, con administración privada de autora.

## Publicar en GitHub y Render

1. Descomprime este ZIP. En GitHub crea un repositorio (puede ser privado) y sube **el contenido de esta carpeta**, de modo que `app.py`, `requirements.txt` y `render.yaml` queden en la raíz del repositorio.
2. En Render selecciona **New + → Blueprint** y conecta ese repositorio. Render leerá `render.yaml`.
3. Cuando Render solicite `ADMIN_PASSWORD`, escribe una contraseña nueva, larga y exclusiva. El usuario ya está configurado como `paguiblue`.
4. Confirma la creación del servicio. El archivo `render.yaml` configura Gunicorn y un Persistent Disk en `/var/data` para conservar la base de datos, portadas y archivos.
5. Cuando el servicio termine de desplegarse, abre `https://TU-SERVICIO.onrender.com/admin/entrar` e inicia sesión.

> **Importante sobre costos:** el Blueprint usa un servicio Starter y un Persistent Disk de 1 GB. Render requiere un plan compatible/pagado para discos persistentes. Si eliges un servicio gratuito sin disco, los datos guardados en el sistema de archivos pueden perderse al reiniciar o desplegar.

## Configuración equivalente manual

Si creas un Web Service manualmente:

- Runtime: Python
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`
- Health Check Path: `/`
- Añade un Persistent Disk montado en `/var/data` (1 GB como mínimo).
- Variables de entorno:
  - `SECRET_KEY`: cadena larga y aleatoria (no la publiques).
  - `ADMIN_USERNAME`: `paguiblue`
  - `ADMIN_PASSWORD`: tu contraseña nueva y privada.
  - `DATABASE_PATH`: `/var/data/gabinete.db`
  - `UPLOADS_DIR`: `/var/data/uploads`

## Desarrollo local en Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m flask --app app run --debug
```

Abre `http://127.0.0.1:5000`. El usuario local predeterminado es `paguiblue` y la contraseña inicial del código es `P4gu!Blue_31Oct#26`; cámbiala mediante la variable de entorno `ADMIN_PASSWORD` incluso en desarrollo si deseas usar otra.

## Funciones

- Sitio público para leer novelas y capítulos.
- Acceso privado de autora para crear, editar y eliminar novelas, capítulos, poemas y libros.
- Biblioteca personal, galería, playlists y diario privado.
- SQLite y archivos cargados en el directorio configurado.

No subas contraseñas, archivos `.env`, ni la base de datos personal al repositorio. Configura los secretos desde Render.
