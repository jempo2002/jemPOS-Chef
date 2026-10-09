# jemPOS Chef

POS y contabilidad en la nube para restaurantes. Versión de jemPOS adaptada: mesas, comandas, cocina, propinas, cuenta dividida y recetas con inventario de ingredientes.

## Ramas

Solo hay dos: `main` (lo aprobado) y `test` (donde se trabaja). Todo cambio va a `test`; cuando jempo lo aprueba o corrige, se pasa a `main` con un PR de `test` a `main`.

## App (Flask)

Base extraída de jemPOS (que no se modifica): login, recuperación de contraseña, sesión revalidada en cada petición, modo solo lectura al vencer la suscripción, cabeceras de seguridad. Lo propio de Chef:

- **Sedes**: cada restaurante tiene al menos una. Cajero, Mesero y Cocina están atados a la suya; el Admin elige sede al entrar si hay varias. Borrado lógico (`estado = 'Eliminada'`).
- **Planes** (`app/services/plan_service.py`): Básico $49.000, Completo $69.000, Cadena $99.000. Recetas, cocina, cuenta dividida y multisede desde Completo (`requiere_funcion("recetas")`); factura electrónica solo en Cadena.
- **Multisede**: 2 sedes incluidas en Completo y Cadena y se pueden abrir más; 1 en Básico. El mismo Admin maneja todas y hay hasta 2 Admin (1 en Básico). Cada sede adicional paga el 50 % del plan al mes y un montaje único de $79.000, que el Master marca cobrado. Triggers en la base rechazan una segunda sede en Básico o bajar a Básico con varias sedes.
- **Panel Master** (`/panel-master`): crea restaurantes (tienda + sede principal + Admin, con un mes de prueba), cambia el plan, registra pagos y elimina.
- **Recetas e insumos** (`/recetas`, Completo y Cadena): insumos en gramos, mililitros o unidades con stock por sede, compras en kg/lb/L con costo promedio ponderado, recetas por plato y costo por plato con alerta cuando los ingredientes pasan del 35 % del precio (ajustable por restaurante). Al enviar la comanda a cocina se descuentan los ingredientes, validando el consumo sumado por insumo. Insumos y líneas de receta con borrado suave; el kardex (`movimientos_inventario`) no se edita ni se borra (triggers en la base).
- **Cuenta dividida** (Completo y Cadena, solo Cajero y Admin): botón "Dividir cuenta" en el pedido. Por productos, cada persona toma unidades de las líneas (2 cervezas se parten 1 y 1) y se cobra como una venta propia con su método y su propina. En partes iguales, el total se reparte en N montos y queda una sola venta con los platos reales; si mezclan efectivo con otro método, la venta queda Mixto. Cada parte queda en `pedido_cuentas`. Todo o nada, en una transacción, y reenviarlo desde la cola sin conexión no cobra dos veces.
- **Para llevar** (todos los planes): botón "Nuevo para llevar" en Mesas. Funciona igual que una mesa (carta, enviar a cocina, precuenta, cobro, dividir), con nombre y teléfono del cliente y un número por sede (#1, #2…). El dispositivo le pone un uuid, así que se puede llenar sin conexión. Se puede cobrar antes de que la cocina termine; sale de la lista cuando se marca "Entregado al cliente" (o sola, 12 horas después de cobrado).
- **Domicilios** (todos los planes): menú Domicilios. Se toman como un para llevar, con dirección. "Despachar" asigna el domiciliario, cómo paga el cliente (efectivo, Nequi, tarjeta o Mixto) y con cuánto paga, e imprime la hoja del domiciliario con lo que debe cobrar y el vuelto. En **Caja › Domiciliarios** hay una tarjeta por domiciliario con el efectivo exacto que debe entregar al regresar (solo cuenta lo no cobrado en efectivo, o la parte en efectivo de un Mixto), en rojo si pasa el tope (Admin lo ajusta, $150.000 por defecto). "Recibí $X" crea las ventas en la caja abierta y suma el efectivo al cajón; si la cifra cambió mientras tanto, no registra nada y avisa. Cambiar el método en el camino, reasignar domiciliario y "No lo entregó" son solo de Cajero y Admin y quedan en auditoría. Domiciliarios con borrado suave; no son usuarios ni gastan cupo del plan.
- **Redis** solo para sesiones y contadores de intentos de login. Obligatorio en producción.

### Arrancar en local

```bash
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env            # FLASK_ENV=development y datos de la base
python scripts/run_migration.py db/schema.sql
python scripts/run_migration.py migrations/2026-10-07_sedes_planes_roles.sql
python scripts/run_migration.py migrations/2026-10-07_turnos_mesas_comandas.sql
python scripts/run_migration.py migrations/2026-10-07b_recetas_insumos.sql
python scripts/run_migration.py migrations/2026-10-07c_cuenta_dividida_llevar.sql
python scripts/run_migration.py migrations/2026-10-07d_domicilios_recaudo.sql
python scripts/run_migration.py migrations/2026-10-07e_pago_mixto.sql
python scripts/run_migration.py migrations/2026-10-09_multisede_sin_tope.sql
python scripts/crear_master.py "Tu nombre" tu@correo.com
python run.py                   # http://127.0.0.1:5000
```

### Migrar la base de Railway desde Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\migrar_railway.ps1
```

Toma la `MYSQL_PUBLIC_URL` del servicio MySQL (Railway › MySQL › Variables) de una línea `MYSQL_PUBLIC_URL=...` en el `.env` local, o la pide sin mostrarla, y aplica `db/schema.sql` y todas las migraciones en orden. Se puede repetir sin riesgo. Con `-Nombre "Tu nombre" -Correo tu@correo.com` crea además el usuario Master en esa base.

### Probar mesas y comandas con datos de ejemplo

```bash
python scripts/crear_demo.py    # crea "Demo Chef": 10 mesas, carta, insumos, 4 recetas y un usuario por rol
```

Usuarios `admin@`, `mesero@`, `cocina@` y `cajero@demo.chef` (misma contrasena). Prueba: Cajero abre caja en `/caja`; Mesero abre una mesa en `/mesas`, agrega platos y manda la comanda; Cocina la ve en `/cocina` y la avanza; Cajero cobra la mesa, o la divide con "Dividir cuenta". Para llevar: "Nuevo para llevar" en `/mesas`.

### Pruebas

Necesitan una base MariaDB/MySQL (se borra y se crea `chef_pytest`) y, opcionalmente, Redis:

```bash
TEST_DB_USER=usuario TEST_DB_PASSWORD=clave TEST_REDIS_URL=redis://localhost:6379/15 pytest
```

## Landing

Sitio estático en `index.html`, `css/landing.css` y `js/landing.js`. Mismo lenguaje visual que el landing de jemPOS (Inter, tarjetas, sombras suaves, entrada escalonada, botón con brillo) con paleta naranja.

Para verlo: `python3 -m http.server 8000` y abrir http://localhost:8000.

Precios: Básico $49.000, Completo $69.000, Cadena $99.000 al mes (sede extra al 50 % del plan, montaje $79.000); implementación $119.000 / $219.000 / $379.000.

Pendiente: cifras reales y respuesta sobre funcionamiento sin conexión.
