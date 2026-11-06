# Desarrollo local con Docker Compose

## Alcance

El entorno local inicial de MIRA contiene únicamente PostgreSQL 18.6 para la primera vertical slice. No incluye Django, Next.js, Celery, Redis, pagos, servicios cloud ni datos reales.

La configuración usa la imagen oficial `postgres:18.6`, con soporte multi-arquitectura incluido ARM64 para Apple Silicon. PostgreSQL se publica sólo en `127.0.0.1`; no queda expuesto en interfaces de red externas. El servicio usa una red bridge privada y acotada al proyecto Compose, no una red externa compartida. El volumen se monta en `/var/lib/postgresql`, que es la ruta correcta para PostgreSQL 18.

## Requisitos

- Docker Desktop en ejecución.
- Docker Compose v2 disponible como `docker compose`.
- Un puerto local libre: MIRA usa `55432` por defecto.

No use credenciales de producción, datos personales, dumps ni información comercial en este entorno.

## Configuración inicial

Desde la raíz del repositorio:

```bash
cp .env.example .env
```

Edite `.env` y reemplace `POSTGRES_PASSWORD` por una contraseña local única. `.env` está ignorado por Git; `.env.example` es la plantilla versionada y no contiene secretos.

Antes de ejecutar Django, agregue un `DJANGO_SECRET_KEY` local único en `.env`. No copie, imprima ni versione ese archivo.

Valide la configuración antes de descargar o iniciar servicios:

```bash
docker compose version
docker compose config -q
docker compose pull
mise install --locked python node pnpm uv
```

## Operación diaria

Inicie sólo PostgreSQL:

```bash
docker compose up -d db
docker compose ps
```

Espere hasta que el estado de `db` indique `healthy`. Consulte los logs si no alcanza ese estado:

```bash
docker compose logs db
```

Para verificar disponibilidad con una consulta simple:

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1;"'
```

Detenga y conserve el contenedor y el volumen:

```bash
docker compose down
```

## Conexión desde Django

Con PostgreSQL iniciado, Django se conectará desde el host local a:

```text
HOST=127.0.0.1
PORT=<POSTGRES_PORT de .env>
NAME=<POSTGRES_DB de .env>
USER=<POSTGRES_USER de .env>
PASSWORD=<POSTGRES_PASSWORD de .env>
```

La aplicación futura debe leer esos valores desde variables de entorno locales y nunca incluirlos en código, fixtures ni archivos versionados.

Desde `apps/api`, cargue el archivo local sin agregar una dependencia dotenv:

```bash
uv run --env-file ../../.env python manage.py check
```

Para la Web/PWA, copie `apps/web/.env.example` a `apps/web/.env.local` si necesita cambiar `MIRA_API_BASE_URL`. El proceso de Next.js no recibe el contenido completo del `.env` raíz.

## Datos y limpieza

`mira_postgres_data` es un volumen nombrado persistente. `docker compose down` detiene y elimina los recursos de Compose, pero conserva ese volumen y los datos locales. Al volver a ejecutar `docker compose up -d db`, PostgreSQL reutiliza esos datos.

Para eliminar el entorno local **incluidos sus datos**, ejecute:

```bash
docker compose down --volumes
```

**Advertencia:** `docker compose down --volumes` destruye permanentemente los datos de desarrollo almacenados en el volumen de este proyecto. Verifique el proyecto y los volúmenes que Compose muestra antes de confirmarlo. No use comandos de eliminación de volúmenes globales ni elimine volúmenes preexistentes de otros proyectos.

## Límites de seguridad

- El puerto está ligado explícitamente a `127.0.0.1`.
- La red bridge de Compose es privada del proyecto y sólo contiene el servicio `db`; no se conecta a una red externa declarada.
- La contraseña es obligatoria y sólo se define en `.env` local.
- No se publican secretos, datos reales ni documentos de `private/`.
