# CLAUDE.md

jemPOS Chef es el POS y la contabilidad en la nube para restaurantes (mesas, comandas, cocina, domicilios, recetas). Está construido sobre jemPOS. Lee `README.md` (arquitectura, planes, comandos) y `FUNCIONALIDADES.md` (qué hace hoy) antes de proponer o escribir código.

## Ramas
- `main`: lo aprobado. Nada llega a `main` sin confirmación explícita de jempo.
- `test`: donde se trabaja. Cada cambio va a `test` y de ahí pasa a `main` con un PR de `test` a `main`.

## Base: jemPOS
- jemPOS (`jempo2002/jemPOS`) no se modifica desde este proyecto: solo se copian piezas.
- Si algo ya existe en jemPOS (login, seguridad, planes, panel Master, páginas legales), tómalo de allí y adáptalo; no lo vuelvas a crear.
- Se conservan sus nombres (`tiendas`, `id_tienda`) para portar piezas sin renombrar.

## Normas
Las normas de seguridad, usabilidad, UX/UI y rendimiento (S/U/D/R1–R31) y su registro de cambios están en [`docs/NORMAS.md`](docs/NORMAS.md). Revísalas antes de tocar una interfaz. Cuando aplique una norma, registra el resultado con una entrada `note(perf)` o la del área que corresponda.

## Cómo trabajar
- **Un hilo por interfaz o problema.** Primero se revisa (solo lectura, con ✅ cumple / 🔧 aplicar / ➖ no aplica) y después se ejecutan los cambios, una interfaz a la vez. Si aparece un problema en otra, se anota como pendiente en vez de arreglarlo en el mismo cambio.
- **Capas:** la lógica de negocio vive en `app/services/`; las rutas en `app/routes/` solo orquestan.
- **Roles y plan:** respeta Master, Admin, Cajero, Mesero y Cocina, la sede de cada usuario y las funciones por plan (`requiere_funcion(...)`).
- **Mobile-first** desde 320 px, objetivos táctiles de 44 px, textos en español con tildes.
- **Paleta:** sin morados, violetas ni índigos.
- **Seguridad:** SQL siempre parametrizado, sin `|safe` con datos del usuario, `@limiter.limit(...)` en rutas sensibles, secretos solo por variables de entorno.
- Funciones que cambian o se agregan: actualiza `FUNCIONALIDADES.md` en el mismo PR.

## Pruebas
```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```
Corre `pytest` (carpeta `tests/`) después de cada cambio y antes de abrir el PR.

## Migraciones de base de datos
Cada migración es un archivo nuevo en `migrations/` (nunca se edita uno ya aplicado) y se aplica con `python scripts/run_migration.py <archivo>`.

Siempre que una tarea necesite una migración nueva:

1. La respuesta final al usuario incluye el SQL completo de cada migración en un bloque de código listo para copiar y pegar, uno por migración y en el orden en que se deben aplicar.
2. Debajo de cada bloque va una línea que diga qué hace la migración y si toca datos existentes o solo crea estructura.
3. Si la migración debe aplicarse antes de desplegar el código (porque el código nuevo falla sin ella), se dice explícitamente.

No basta con nombrar el archivo o la ruta: el usuario aplica las migraciones pegando el SQL a mano.
