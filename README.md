# jemPOS Chef

POS y contabilidad en la nube para restaurantes. Versión de jemPOS adaptada: mesas, comandas, cocina, propinas, cuenta dividida y recetas con inventario de ingredientes.

## App (Flask)

Base extraída de jemPOS (que no se modifica): login, recuperación de contraseña, sesión revalidada en cada petición, modo solo lectura al vencer la suscripción, cabeceras de seguridad. Lo propio de Chef:

- **Sedes**: cada restaurante tiene al menos una. Cajero, Mesero y Cocina están atados a la suya; el Admin elige sede al entrar si hay varias. Borrado lógico (`estado = 'Eliminada'`).
- **Planes** (`app/services/plan_service.py`): Básico $49.000, Completo $69.000, Cadena $89.000. Recetas, cocina, cuenta dividida y multisede desde Completo (`requiere_funcion("recetas")`); factura electrónica solo en Cadena.
- **Multisede**: hasta 4 sedes en Completo y Cadena, 1 en Básico. Cada sede extra paga el 50 % del plan al mes y un montaje único de $79.000, que el Master marca cobrado. Triggers en la base rechazan una sede de más o bajar a Básico con varias sedes.
- **Panel Master** (`/panel-master`): crea restaurantes (tienda + sede principal + Admin, con un mes de prueba), cambia el plan, registra pagos y elimina.
- **Redis** solo para sesiones y contadores de intentos de login. Obligatorio en producción.

### Arrancar en local

```bash
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # FLASK_ENV=development y datos de la base
python scripts/run_migration.py db/schema.sql
python scripts/run_migration.py migrations/2026-10-07_sedes_planes_roles.sql
python scripts/crear_master.py "Tu nombre" tu@correo.com
python run.py                   # http://127.0.0.1:5000
```

### Pruebas

Necesitan una base MariaDB/MySQL (se borra y se crea `chef_pytest`) y, opcionalmente, Redis:

```bash
TEST_DB_USER=usuario TEST_DB_PASSWORD=clave TEST_REDIS_URL=redis://localhost:6379/15 pytest
```

## Landing

Sitio estático en `index.html`, `css/landing.css` y `js/landing.js`. Mismo lenguaje visual que el landing de jemPOS (Inter, tarjetas, sombras suaves, entrada escalonada, botón con brillo) con paleta naranja.

Para verlo: `python3 -m http.server 8000` y abrir http://localhost:8000.

Precios: Básico $49.000, Completo $69.000, Cadena $89.000 al mes (sede extra al 50 % del plan, montaje $79.000); implementación $119.000 / $219.000 / $379.000.

Pendiente: cifras reales y respuesta sobre funcionamiento sin conexión.
